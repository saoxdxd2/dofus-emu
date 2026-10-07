package main

import (
	"archive/zip"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Artifact struct {
	Name        string
	URL         string
	ZipFilename string
	ExpectedLen int64
	// StripPrefix strips leading directory from zip path
	StripPrefix string
	// TargetSubDir is relative to SDK root where extracted files go
	TargetSubDir string
	CheckFile    string // Relative to SDK root to verify installation
}

var artifacts = []Artifact{
	{
		Name:         "platform-tools",
		URL:          "https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip",
		ZipFilename:  "platform-tools.zip",
		ExpectedLen:  8044989,
		StripPrefix:  "",
		TargetSubDir: "",
		CheckFile:    "platform-tools/adb.exe",
	},
	{
		Name:         "cmdline-tools",
		URL:          "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
		ZipFilename:  "commandlinetools.zip",
		ExpectedLen:  149867056,
		StripPrefix:  "cmdline-tools",
		TargetSubDir: "cmdline-tools/latest",
		CheckFile:    "cmdline-tools/latest/bin/avdmanager.bat",
	},
	{
		Name:         "emulator",
		URL:          "https://dl.google.com/android/repository/emulator-windows_x64-16433917.zip",
		ZipFilename:  "emulator.zip",
		ExpectedLen:  459420448,
		StripPrefix:  "",
		TargetSubDir: "",
		CheckFile:    "emulator/emulator.exe",
	},
	{
		Name:         "system-image (Android 10 x86_64)",
		URL:          "https://dl.google.com/android/repository/sys-img/android/x86_64-29_r08-windows.zip",
		ZipFilename:  "sysimg.zip",
		ExpectedLen:  689676765,
		StripPrefix:  "x86_64",
		TargetSubDir: "system-images/android-29/default/x86_64",
		CheckFile:    "system-images/android-29/default/x86_64/system.img",
	},
}

func main() {
	var (
		sdkDir      string
		cacheDir    string
		workers     int
		verifyOnly  bool
		extractOnly bool
	)

	defaultCache := filepath.Join(os.TempDir(), "dl")
	flag.StringVar(&sdkDir, "sdk", "", "Target Android SDK root directory (required)")
	flag.StringVar(&cacheDir, "cache", defaultCache, "Download archive cache directory")
	flag.IntVar(&workers, "workers", 16, "Number of concurrent download segments")
	flag.BoolVar(&verifyOnly, "verify-only", false, "Verify existing SDK installation and exit")
	flag.BoolVar(&extractOnly, "extract-only", false, "Extract existing archives without downloading")
	flag.Parse()

	if sdkDir == "" {
		fmt.Println("[err] -sdk parameter is required (e.g. -sdk C:\\DofusFarm\\sdk)")
		os.Exit(1)
	}

	absSdk, err := filepath.Abs(sdkDir)
	if err != nil {
		fmt.Printf("[err] Invalid SDK directory: %v\n", err)
		os.Exit(1)
	}

	if verifyOnly {
		if verifySDK(absSdk) {
			fmt.Println("[ok] SDK installation verified successfully.")
			os.Exit(0)
		} else {
			fmt.Println("[fail] SDK verification failed.")
			os.Exit(1)
		}
	}

	if err := os.MkdirAll(absSdk, 0755); err != nil {
		fmt.Printf("[err] Failed to create SDK directory %s: %v\n", absSdk, err)
		os.Exit(1)
	}

	if err := os.MkdirAll(cacheDir, 0755); err != nil {
		fmt.Printf("[err] Failed to create cache directory %s: %v\n", cacheDir, err)
		os.Exit(1)
	}

	fmt.Println("==========================================================")
	fmt.Println("   High-Efficiency Android SDK Go Downloader & Deployer   ")
	fmt.Printf("   SDK Target : %s\n", absSdk)
	fmt.Printf("   Cache Dir  : %s\n", cacheDir)
	fmt.Printf("   Concurrency: %d parallel segments\n", workers)
	fmt.Println("==========================================================")

	allStart := time.Now()

	for idx, art := range artifacts {
		checkPath := filepath.Join(absSdk, filepath.FromSlash(art.CheckFile))
		if _, err := os.Stat(checkPath); err == nil {
			fmt.Printf("[%d/%d] [ok] %s already present at %s\n", idx+1, len(artifacts), art.Name, art.CheckFile)
			continue
		}

		zipPath := filepath.Join(cacheDir, art.ZipFilename)

		if !extractOnly {
			fmt.Printf("\n[%d/%d] Fetching %s (%s)...\n", idx+1, len(artifacts), art.Name, art.ZipFilename)
			err := downloadArtifact(art, zipPath, workers)
			if err != nil {
				fmt.Printf("[fail] Download failed for %s: %v\n", art.Name, err)
				os.Exit(1)
			}
		}

		fmt.Printf("[%d/%d] Fast-extracting %s into %s...\n", idx+1, len(artifacts), art.ZipFilename, filepath.Join(absSdk, filepath.FromSlash(art.TargetSubDir)))
		extractStart := time.Now()
		err = extractZip(zipPath, absSdk, art)
		if err != nil {
			fmt.Printf("[fail] Extraction failed for %s: %v\n", art.ZipFilename, err)
			os.Exit(1)
		}
		fmt.Printf("  [ok] Extracted in %s\n", time.Since(extractStart).Round(time.Millisecond))
	}

	// Synthesize emulator package.xml if needed
	ensureEmulatorPackageXml(absSdk)

	fmt.Println("\n==> Verifying unpacked SDK layout...")
	if verifySDK(absSdk) {
		fmt.Printf("\n[ok] SDK setup completed in %s.\n", time.Since(allStart).Round(time.Millisecond))
		os.Exit(0)
	} else {
		fmt.Println("\n[fail] One or more SDK components missing after extraction.")
		os.Exit(1)
	}
}

func downloadArtifact(art Artifact, destPath string, concurrency int) error {
	// Check if cached file exists and has correct size
	if fi, err := os.Stat(destPath); err == nil {
		if art.ExpectedLen > 0 && fi.Size() == art.ExpectedLen {
			fmt.Printf("  [cache] %s matches expected size (%d bytes), skipping download.\n", filepath.Base(destPath), fi.Size())
			return nil
		}
	}

	client := &http.Client{
		Timeout: 30 * time.Minute,
	}

	// 1. Send HEAD request to get content length and check Range support
	req, err := http.NewRequest("HEAD", art.URL, nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP HEAD returned status %d", resp.StatusCode)
	}

	contentLength := resp.ContentLength
	if contentLength <= 0 && art.ExpectedLen > 0 {
		contentLength = art.ExpectedLen
	}

	acceptRanges := resp.Header.Get("Accept-Ranges") == "bytes" || resp.StatusCode == 206 || contentLength > 10*1024*1024

	// If no range support or file is small (< 5MB), download directly
	if !acceptRanges || concurrency <= 1 || contentLength <= 5*1024*1024 {
		return downloadDirect(client, art.URL, destPath, contentLength)
	}

	// Multi-segment parallel download
	return downloadSegmented(client, art.URL, destPath, contentLength, concurrency)
}

func downloadDirect(client *http.Client, url, destPath string, totalSize int64) error {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP GET returned %d", resp.StatusCode)
	}

	partPath := destPath + ".part"
	f, err := os.Create(partPath)
	if err != nil {
		return err
	}

	buf := make([]byte, 64*1024)
	var downloaded int64
	lastReport := time.Now()
	start := time.Now()

	for {
		n, rErr := resp.Body.Read(buf)
		if n > 0 {
			if _, wErr := f.Write(buf[:n]); wErr != nil {
				f.Close()
				return wErr
			}
			downloaded += int64(n)
			if time.Since(lastReport) > 500*time.Millisecond {
				printProgress(downloaded, totalSize, start)
				lastReport = time.Now()
			}
		}
		if rErr != nil {
			if rErr == io.EOF {
				break
			}
			f.Close()
			return rErr
		}
	}
	f.Close()
	printProgress(downloaded, totalSize, start)
	fmt.Println()

	return os.Rename(partPath, destPath)
}

func downloadSegmented(client *http.Client, url, destPath string, totalSize int64, numWorkers int) error {
	partPath := destPath + ".part"
	f, err := os.Create(partPath)
	if err != nil {
		return err
	}

	// Preallocate file size
	if err := f.Truncate(totalSize); err != nil {
		f.Close()
		return err
	}

	chunkSize := totalSize / int64(numWorkers)
	var downloaded int64
	start := time.Now()

	type chunk struct {
		index int
		start int64
		end   int64
	}

	chunks := make([]chunk, numWorkers)
	for i := 0; i < numWorkers; i++ {
		cStart := int64(i) * chunkSize
		cEnd := cStart + chunkSize - 1
		if i == numWorkers-1 {
			cEnd = totalSize - 1
		}
		chunks[i] = chunk{index: i, start: cStart, end: cEnd}
	}

	var wg sync.WaitGroup
	errCh := make(chan error, numWorkers)
	chunkCh := make(chan chunk, numWorkers)
	for _, c := range chunks {
		chunkCh <- c
	}
	close(chunkCh)

	// Stop ticker for progress reporting
	stopProgress := make(chan struct{})
	go func() {
		ticker := time.NewTicker(300 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				cur := atomic.LoadInt64(&downloaded)
				printProgress(cur, totalSize, start)
			case <-stopProgress:
				return
			}
		}
	}()

	for w := 0; w < numWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			workerBuf := make([]byte, 64*1024)

			for c := range chunkCh {
				req, err := http.NewRequest("GET", url, nil)
				if err != nil {
					errCh <- err
					return
				}
				req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", c.start, c.end))

				resp, err := client.Do(req)
				if err != nil {
					errCh <- err
					return
				}

				if resp.StatusCode != http.StatusPartialContent && resp.StatusCode != http.StatusOK {
					resp.Body.Close()
					errCh <- fmt.Errorf("range request failed for bytes %d-%d with status %d", c.start, c.end, resp.StatusCode)
					return
				}

				writeOffset := c.start
				for {
					n, rErr := resp.Body.Read(workerBuf)
					if n > 0 {
						if _, wErr := f.WriteAt(workerBuf[:n], writeOffset); wErr != nil {
							resp.Body.Close()
							errCh <- wErr
							return
						}
						writeOffset += int64(n)
						atomic.AddInt64(&downloaded, int64(n))
					}
					if rErr != nil {
						if rErr == io.EOF {
							break
						}
						resp.Body.Close()
						errCh <- rErr
						return
					}
				}
				resp.Body.Close()
			}
		}()
	}

	wg.Wait()
	close(stopProgress)
	f.Close()

	select {
	case err := <-errCh:
		os.Remove(partPath)
		return err
	default:
	}

	cur := atomic.LoadInt64(&downloaded)
	printProgress(cur, totalSize, start)
	fmt.Println()

	if cur != totalSize {
		return fmt.Errorf("download size mismatch: got %d bytes, expected %d bytes", cur, totalSize)
	}

	return os.Rename(partPath, destPath)
}

func printProgress(current, total int64, start time.Time) {
	elapsed := time.Since(start).Seconds()
	if elapsed <= 0 {
		elapsed = 0.001
	}
	mbps := (float64(current) / 1024 / 1024) / elapsed

	pct := float64(0)
	if total > 0 {
		pct = (float64(current) / float64(total)) * 100.0
	}

	curMB := float64(current) / 1024 / 1024
	totMB := float64(total) / 1024 / 1024

	fmt.Printf("\r  [dl] %5.1f%% (%6.1f MB / %6.1f MB) [%5.1f MB/s] ", pct, curMB, totMB, mbps)
	os.Stdout.Sync()
}

func extractZip(zipPath, sdkRoot string, art Artifact) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()

	destBase := sdkRoot
	if art.TargetSubDir != "" {
		destBase = filepath.Join(sdkRoot, filepath.FromSlash(art.TargetSubDir))
	}
	if err := os.MkdirAll(destBase, 0755); err != nil {
		return err
	}

	// Concurrently extract files using worker pool
	type task struct {
		f *zip.File
	}

	taskCh := make(chan task, len(r.File))
	errCh := make(chan error, 8)
	var wg sync.WaitGroup

	numExtractors := 4
	for w := 0; w < numExtractors; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, 128*1024)

			for t := range taskCh {
				f := t.f
				cleanName := f.Name

				// Handle prefix stripping
				if art.StripPrefix != "" {
					prefixWithSlash := art.StripPrefix + "/"
					if strings.HasPrefix(cleanName, prefixWithSlash) {
						cleanName = strings.TrimPrefix(cleanName, prefixWithSlash)
					} else if cleanName == art.StripPrefix {
						continue // skip root dir
					}
				}

				if cleanName == "" || strings.HasPrefix(cleanName, "..") {
					continue
				}

				targetPath := filepath.Join(destBase, filepath.FromSlash(cleanName))

				if f.FileInfo().IsDir() {
					os.MkdirAll(targetPath, 0755)
					continue
				}

				if err := os.MkdirAll(filepath.Dir(targetPath), 0755); err != nil {
					select {
					case errCh <- err:
					default:
					}
					return
				}

				rc, err := f.Open()
				if err != nil {
					select {
					case errCh <- err:
					default:
					}
					return
				}

				out, err := os.OpenFile(targetPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, f.Mode())
				if err != nil {
					rc.Close()
					select {
					case errCh <- err:
					default:
					}
					return
				}

				_, err = io.CopyBuffer(out, rc, buf)
				rc.Close()
				out.Close()

				if err != nil {
					select {
					case errCh <- err:
					default:
					}
					return
				}
			}
		}()
	}

	for _, f := range r.File {
		taskCh <- task{f: f}
	}
	close(taskCh)
	wg.Wait()

	select {
	case err := <-errCh:
		return err
	default:
		return nil
	}
}

func ensureEmulatorPackageXml(sdkRoot string) {
	pkgXml := filepath.Join(sdkRoot, "emulator", "package.xml")
	if _, err := os.Stat(pkgXml); err == nil {
		return
	}

	xmlContent := `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` + "\n" +
		`<ns2:repository xmlns:ns2="http://schemas.android.com/repository/android/common/02" xmlns:ns3="http://schemas.android.com/repository/android/common/01" xmlns:ns4="http://schemas.android.com/repository/android/generic/01" xmlns:ns5="http://schemas.android.com/repository/android/generic/02" xmlns:ns9="http://schemas.android.com/sdk/android/repo/repository2/01" xmlns:ns10="http://schemas.android.com/sdk/android/repo/repository2/02" xmlns:ns11="http://schemas.android.com/sdk/android/repo/repository2/03">` + "\n" +
		`  <license id="license-24333f" type="text"/>` + "\n" +
		`  <localPackage path="emulator" obsolete="false">` + "\n" +
		`    <type-details xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="ns5:genericDetailsType"/>` + "\n" +
		`    <revision><major>37</major><minor>3</minor><micro>2</micro></revision>` + "\n" +
		`    <display-name>Android Emulator</display-name>` + "\n" +
		`    <uses-license ref="license-24333f"/>` + "\n" +
		`  </localPackage>` + "\n" +
		`</ns2:repository>` + "\n"

	os.MkdirAll(filepath.Dir(pkgXml), 0755)
	if err := os.WriteFile(pkgXml, []byte(xmlContent), 0644); err == nil {
		fmt.Println("  [ok] Created synthesized emulator/package.xml")
	}
}

func verifySDK(sdkRoot string) bool {
	checks := []string{
		"emulator/emulator.exe",
		"platform-tools/adb.exe",
		"system-images/android-29/default/x86_64/system.img",
		"cmdline-tools/latest/bin/avdmanager.bat",
		"emulator/package.xml",
	}

	allOk := true
	for _, c := range checks {
		p := filepath.Join(sdkRoot, filepath.FromSlash(c))
		if fi, err := os.Stat(p); err == nil && fi.Size() > 0 {
			fmt.Printf("  [OK]      %s (%d bytes)\n", c, fi.Size())
		} else {
			fmt.Printf("  [MISSING] %s\n", c)
			allOk = false
		}
	}
	return allOk
}
