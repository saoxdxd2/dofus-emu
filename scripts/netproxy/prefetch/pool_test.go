package prefetch

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestNewClampsValues(t *testing.T) {
	// workers clamped to [1, 4]
	p := New(0, 10, 10, func(context.Context, Job) {})
	if p.workers != 1 {
		t.Fatalf("expected workers clamped to 1, got %d", p.workers)
	}
	p = New(10, 10, 10, func(context.Context, Job) {})
	if p.workers != 4 {
		t.Fatalf("expected workers clamped to 4, got %d", p.workers)
	}
	// queue clamped to >= 1
	p = New(1, 0, 10, func(context.Context, Job) {})
	if p.highQ == nil || p.specQ == nil {
		t.Fatal("expected channels to be created for queue=0")
	}
	// perSecond clamped to > 0
	p = New(1, 10, 0, func(context.Context, Job) {})
	if p.limit == nil {
		t.Fatal("expected limiter to be created")
	}
}

func TestSubmitDropsWhenLanesFull(t *testing.T) {
	p := New(1, 1, 1000, func(context.Context, Job) {})
	defer p.Close()
	p.Start()

	if !p.Submit(Job{Target: "a", ID: 1}) {
		t.Fatal("first submit should succeed")
	}
	if !p.Submit(Job{Target: "b", ID: 2}) {
		t.Fatal("second submit should succeed (spec lane)")
	}
	if p.Submit(Job{Target: "c", ID: 3}) {
		t.Fatal("third submit should drop when both lanes full")
	}
}

func TestSubmitDedupInFlight(t *testing.T) {
	p := New(1, 10, 1000, func(context.Context, Job) {})
	defer p.Close()
	p.Start()

	if !p.Submit(Job{Target: "a", ID: 1}) {
		t.Fatal("first submit should succeed")
	}
	if p.Submit(Job{Target: "a", ID: 1}) {
		t.Fatal("duplicate in-flight submit should drop")
	}
	if !p.Submit(Job{Target: "a", ID: 2}) {
		t.Fatal("different ID submit should succeed")
	}
}

func TestJobIsProcessed(t *testing.T) {
	var processed atomic.Int32
	var mu sync.Mutex
	var last Job
	p := New(2, 10, 1000, func(_ context.Context, j Job) {
		mu.Lock()
		last = j
		mu.Unlock()
		processed.Add(1)
	})
	defer p.Close()
	p.Start()

	j := Job{Target: "t", ID: 42, Depth: 1}
	if !p.Submit(j) {
		t.Fatal("submit should succeed")
	}
	p.Wait(5 * time.Second)
	if processed.Load() != 1 {
		t.Fatalf("expected 1 job processed, got %d", processed.Load())
	}
	mu.Lock()
	defer mu.Unlock()
	if last != j {
		t.Fatalf("expected job %+v, got %+v", j, last)
	}
}

func TestHighPriorityLaneDrainedFirst(t *testing.T) {
	var order []int
	var mu sync.Mutex
	p := New(1, 10, 1000, func(_ context.Context, j Job) {
		mu.Lock()
		order = append(order, int(j.ID))
		mu.Unlock()
	})
	defer p.Close()
	p.Start()

	// Submit high priority first, then speculative. The worker always drains the
	// high-priority lane first, so Depth 1 must be processed before Depth 2.
	if !p.Submit(Job{Target: "t", ID: 1, Depth: 1}) {
		t.Fatal("submit high priority should succeed")
	}
	if !p.Submit(Job{Target: "t", ID: 2, Depth: 2}) {
		t.Fatal("submit speculative should succeed")
	}
	p.Wait(5 * time.Second)
	mu.Lock()
	defer mu.Unlock()
	if len(order) < 2 || order[0] != 1 || order[1] != 2 {
		t.Fatalf("expected high priority first, got order %v", order)
	}
}
