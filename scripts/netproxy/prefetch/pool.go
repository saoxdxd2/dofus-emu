// Package prefetch runs background map warming in a bounded, low-priority
// pool. The data plane must never wait behind it: Submit drops a job when the
// queue is full, and a rate limiter keeps the workers from competing with live
// game traffic for the same egress.
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
	q       chan Job
	workers int
	limit   *rate.Limiter
	fn      func(context.Context, Job)
	ctx     context.Context
	cancel  context.CancelFunc
	once    sync.Once
	closed  sync.Once
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
		q:       make(chan Job, queue),
		workers: workers,
		limit:   rate.NewLimiter(rate.Limit(perSecond), 1),
		fn:      fn,
		ctx:     ctx,
		cancel:  cancel,
	}
}

func (p *Pool) Start() {
	p.once.Do(func() {
		for i := 0; i < p.workers; i++ {
			go p.worker()
		}
	})
}

func (p *Pool) Submit(j Job) bool {
	if p == nil || p.fn == nil {
		return false
	}
	p.Start()
	select {
	case p.q <- j:
		return true
	default:
		return false
	}
}

func (p *Pool) worker() {
	for {
		select {
		case <-p.ctx.Done():
			return
		case j, ok := <-p.q:
			if !ok {
				return
			}
			// Low priority: yield before each job and pace the queue. A warming
			// pass that outruns the limiter is allowed to sit in the channel;
			// the data plane never blocks on it.
			runtime.Gosched()
			if err := p.limit.Wait(p.ctx); err != nil {
				return
			}
			p.fn(p.ctx, j)
		}
	}
}

func (p *Pool) Close() {
	if p == nil {
		return
	}
	p.closed.Do(func() {
		p.cancel()
		close(p.q)
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
		if len(p.q) == 0 {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
}
