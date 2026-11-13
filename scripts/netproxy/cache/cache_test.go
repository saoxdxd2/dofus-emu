package cache

import (
	"bytes"
	"io"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

// TestBudgetConcurrencyAndLimits verifies that concurrent reservations never overshoot Max().
func TestBudgetConcurrencyAndLimits(t *testing.T) {
	const maxBudget int64 = 10 << 20 // 10 MB
	budget := NewBudget(maxBudget)

	var wg sync.WaitGroup
	var totalGranted atomic.Int64
	workers := 50
	perWorkerRequest := int64(512 << 10) // 512 KB

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			granted := budget.Reserve(perWorkerRequest)
			totalGranted.Add(granted)
		}()
	}
	wg.Wait()

	if budget.Used() > budget.Max() {
		t.Fatalf("budget overshot! used=%d max=%d", budget.Used(), budget.Max())
	}
	if totalGranted.Load() != budget.Used() {
		t.Fatalf("sum of granted (%d) != budget.Used (%d)", totalGranted.Load(), budget.Used())
	}

	// Release all
	budget.Release(totalGranted.Load())
	if budget.Used() != 0 {
		t.Fatalf("expected 0 used after full release, got %d", budget.Used())
	}
}

// TestBudgetReserveWithCleanup verifies defer cleanup semantics with ReserveWithCleanup.
func TestBudgetReserveWithCleanup(t *testing.T) {
	budget := NewBudget(1 << 20) // 1 MB

	fn := func() {
		granted, release := budget.ReserveWithCleanup(256 << 10) // 256 KB
		defer release()

		if granted != 256<<10 {
			t.Errorf("expected 256 KB granted, got %d", granted)
		}
		if budget.Used() != 256<<10 {
			t.Errorf("expected 256 KB used, got %d", budget.Used())
		}
		// Calling release twice within defer should still only release once
		defer release()
	}

	fn()

	if budget.Used() != 0 {
		t.Fatalf("expected 0 used after defer release, got %d", budget.Used())
	}
}

// TestBudgetReserveExact verifies atomic exact reservations.
func TestBudgetReserveExact(t *testing.T) {
	budget := NewBudget(1000)

	release1, ok := budget.ReserveExact(600)
	if !ok {
		t.Fatalf("expected first 600 bytes exact reservation to succeed")
	}
	if budget.Used() != 600 {
		t.Fatalf("expected 600 used, got %d", budget.Used())
	}

	// Requesting 500 when 400 is left must fail completely and grant 0
	release2, ok := budget.ReserveExact(500)
	if ok {
		t.Fatalf("expected 500 bytes exact reservation to fail when 400 left")
	}
	release2() // harmless no-op
	if budget.Used() != 600 {
		t.Fatalf("expected 600 used after failed exact reservation, got %d", budget.Used())
	}

	release1()
	if budget.Used() != 0 {
		t.Fatalf("expected 0 used after release1, got %d", budget.Used())
	}
}

// TestBudgetHighWatermarkAndShedding verifies 85% high watermark detection and cache bypass logic.
func TestBudgetHighWatermarkAndShedding(t *testing.T) {
	const maxBudget int64 = 10000
	budget := NewBudget(maxBudget)

	expectedHigh := int64(10000 * 0.85) // 8500
	if budget.HighWatermark() != expectedHigh {
		t.Fatalf("expected high watermark %d, got %d", expectedHigh, budget.HighWatermark())
	}

	if budget.ShedOrBypass(100) {
		t.Fatalf("should not shed when used is 0")
	}

	// Reserve 8400 bytes (84% - just below 85%)
	budget.Reserve(8400)
	if budget.ShedOrBypass(100) {
		t.Fatalf("should not shed at 84%% for small request")
	}
	// Requesting 2000 would exceed 10000 -> must shed/bypass
	if !budget.ShedOrBypass(2000) {
		t.Fatalf("should shed when request cannot fit in remaining budget")
	}

	// Reserve 200 more bytes (total 8600 bytes -> 86%, crosses 85% high watermark)
	budget.Reserve(200)
	if !budget.ShedOrBypass(10) {
		t.Fatalf("must shed/bypass once usage crosses 85%% high watermark")
	}

	budget.Release(8600)
	if budget.ShedOrBypass(100) {
		t.Fatalf("should not shed after release")
	}
}

// TestBufferPool64KB verifies BufferPool returns 64 KB slices backed by sync.Pool.
func TestBufferPool64KB(t *testing.T) {
	pool := NewBufferPool(BufferSize)

	buf1 := pool.Get()
	if len(buf1) != BufferSize {
		t.Fatalf("expected buffer len %d, got %d", BufferSize, len(buf1))
	}

	// Dirty the buffer
	buf1[0] = 0xAA
	buf1[BufferSize-1] = 0xBB

	pool.Put(buf1)

	buf2 := pool.Get()
	if len(buf2) != BufferSize {
		t.Fatalf("expected second buffer len %d, got %d", BufferSize, len(buf2))
	}
	pool.Put(buf2)
}

// TestCopyBoundedWithBudget verifies CopyBounded copies safely through budget limits.
func TestCopyBoundedWithBudget(t *testing.T) {
	budget := NewBudget(1000)
	pool := NewBufferPool(64)

	input := strings.Repeat("A", 800)
	var output bytes.Buffer

	n, err := CopyBounded(&output, strings.NewReader(input), budget, pool, 800)
	if err != nil {
		t.Fatalf("CopyBounded failed: %v", err)
	}
	if n != 800 {
		t.Fatalf("expected 800 bytes written, got %d", n)
	}
	if budget.Used() != 0 {
		t.Fatalf("expected budget.Used == 0 after CopyBounded returns, got %d", budget.Used())
	}

	// LimitReader bounds
	overInput := strings.Repeat("B", 2000)
	var overOutput bytes.Buffer
	n2, err := CopyBounded(&overOutput, strings.NewReader(overInput), budget, pool, 500)
	if err != nil && err != io.EOF {
		t.Fatalf("CopyBounded with limit error: %v", err)
	}
	if n2 != 500 {
		t.Fatalf("expected 500 bytes copied under limit, got %d", n2)
	}
}
