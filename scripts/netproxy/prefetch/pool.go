// Package prefetch runs background map warming in a bounded, low-priority
// pool. The data plane must never wait behind it: Submit drops a job when the
// queue is full, and a rate limiter keeps the workers from competing with live
// game traffic for the same egress.
//
// Two lanes keep immediate neighbor tiles ahead of speculative work: jobs with
// Depth == 1 go on the high-priority lane and are always drained first, while
// Depth > 1 jobs go on the speculative lane. An in-flight map deduplicates
// (Target, ID) pairs so adjacent-map traversal never burns rate-limiter tokens
// on duplicate fetches. A WaitGroup lets Close() wait for in-flight workers to
// finish before returning.
package prefetch

import (
	"context"
	"runtime"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

type Job struct {
	Target string
	ID     int64
	Depth  int
}

type Pool struct {
	highQ    chan Job   // Depth == 1: immediate neighbor tiles, drained first
	specQ    chan Job   // Depth > 1: speculative warming
	workers  int
	limit    *rate.Limiter
	fn       func(context.Context, Job)
	ctx      context.Context
	cancel   context.CancelFunc
	started  sync.Once
	closed   sync.Once
	wg       sync.WaitGroup

	mu      sync.Mutex
	inflight map[[2]interface{}]struct{} // (Target, ID) dedup for in-flight jobs
}

func New(workers, queue int, perSecond float64, fn func(context.Context, Job)) *Pool {
	if workers < 1 {
		workers = 1
	}
	if workers > 4 {
		workers = 4
	}
	if queue < 1 {
		queue = 256
	}
	if perSecond <= 0 {
		perSecond = 8
	}
	ctx, cancel := context.WithCancel(context.Background())
	return &Pool{
		highQ:    make(chan Job, queue),
		specQ:    make(chan Job, queue),
		workers:  workers,
		limit:    rate.NewLimiter(rate.Limit(perSecond), 1),
		fn:       fn,
		ctx:      ctx,
		cancel:   cancel,
		inflight: make(map[[2]interface{}]struct{}),
	}
}

func (p *Pool) Start() {
	p.started.Do(func() {
		for i := 0; i < p.workers; i++ {
			go p.worker()
		}
	})
}

// key returns a dedup key for a job.
func (p *Pool) key(j Job) [2]interface{} {
	return [2]interface{}{j.Target, j.ID}
}

// Submit enqueues a job on the appropriate lane. Returns false if the lane is
// full (drop, never block the data plane). Duplicate in-flight (Target, ID)
// pairs are dropped.
func (p *Pool) Submit(j Job) bool {
	if p == nil || p.fn == nil {
		return false
	}
	p.Start()

	p.mu.Lock()
	if _, seen := p.inflight[p.key(j)]; seen {
		p.mu.Unlock()
		return false // already in flight
	}
	p.inflight[p.key(j)] = struct{}{}
	p.mu.Unlock()

	select {
	case p.highQ <- j:
		return true
	default:
	}
	select {
	case p.specQ <- j:
		return true
	default:
	}
	// Both lanes full: release the dedup entry and drop.
	p.mu.Lock()
	delete(p.inflight, p.key(j))
	p.mu.Unlock()
	return false
}

func (p *Pool) worker() {
	for {
		select {
		case <-p.ctx.Done():
			return
		default:
		}

		// High-priority lane first: immediate neighbor tiles always prefetch
		// before speculative work.
		var j Job
		var ok bool
		select {
		case j, ok = <-p.highQ:
		default:
			select {
			case j, ok = <-p.specQ:
			default:
				// Neither lane has a ready job; block waiting for either.
				select {
				case <-p.ctx.Done():
					return
				case j, ok = <-p.highQ:
				case j, ok = <-p.specQ:
				}
			}
		}
		if !ok {
			return
		}

		// Mark this job no longer in flight once it's been dequeued.
		p.mu.Lock()
		delete(p.inflight, p.key(j))
		p.mu.Unlock()

		p.wg.Add(1)
		// Low priority: yield before each job and pace the queue. A warming
		// pass that outruns the limiter is allowed to sit in the channel;
		// the data plane never blocks on it.
		runtime.Gosched()
		if err := p.limit.Wait(p.ctx); err != nil {
			p.wg.Done()
			return
		}
		p.fn(p.ctx, j)
		p.wg.Done()
	}
}

func (p *Pool) Close() {
	if p == nil {
		return
	}
	p.closed.Do(func() {
		p.cancel()
		close(p.highQ)
		close(p.specQ)
		// Wait for all in-flight workers to finish so Close() never returns
		// while an HTTP fetch is still mid-flight.
		p.wg.Wait()
	})
}

// Wait is a test helper: it gives queued jobs a bounded window to drain
// without exposing the channel.
func (p *Pool) Wait(d time.Duration) {
	if p == nil {
		return
	}
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		p.mu.Lock()
		remaining := len(p.inflight)
		p.mu.Unlock()
		if remaining == 0 && len(p.highQ) == 0 && len(p.specQ) == 0 {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
}
