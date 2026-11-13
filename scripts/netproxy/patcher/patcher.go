// Package patcher applies operator mods to cached assets. JSON documents are
// parsed only inside a hard byte cap; HTML/JS replacements stream through a
// rolling window so a multi-megabyte bundle never has to be materialized just
// to swap a token.
package patcher

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	// MaxJSONPatchBytes bounds a document that has to be parsed as a tree.
	MaxJSONPatchBytes int64 = 8 << 20
	streamBufferSize        = 64 << 10
)

type PatchOp struct {
	Op    string `json:"op"`
	Path  string `json:"path"`
	Value any    `json:"value"`
}

type Rule struct {
	Match       string    `json:"match"`
	Action      string    `json:"action"`
	Status      int       `json:"status"`
	File        string    `json:"file"`
	ContentType string    `json:"contentType"`
	Patch       []PatchOp `json:"patch"`
	// Find/Replace turn Action "replace" into a streaming text patch. They are
	// intentionally separate from Match, which selects the URL.
	Find    string `json:"find"`
	Replace string `json:"replace"`
}

type Loaded struct {
	Rules []Rule
	Regex []*regexp.Regexp
	Mod   time.Time
}

// Load reads rules.json and compiles every Match. A bad individual pattern is
// skipped, not fatal: one typo must not disable every other mod.
func Load(path string) (*Loaded, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var rules []Rule
	if err := json.Unmarshal(raw, &rules); err != nil {
		return nil, err
	}
	res := make([]*regexp.Regexp, len(rules))
	for i := range rules {
		re, err := regexp.Compile(rules[i].Match)
		if err != nil {
			continue
		}
		res[i] = re
	}
	fi, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	return &Loaded{Rules: rules, Regex: res, Mod: fi.ModTime()}, nil
}

// SafeModFile resolves a rule's file under modsDir and rejects absolute paths
// and traversal. The returned path is the only file an override may serve.
func SafeModFile(modsDir, rel string) (string, bool) {
	rel = strings.TrimSpace(rel)
	if rel == "" || filepath.IsAbs(rel) || strings.Contains(rel, "..") {
		return "", false
	}
	return filepath.Join(modsDir, filepath.FromSlash(rel)), true
}

// ApplyJSONPatch parses raw, applies set/remove operations, and marshals the
// result. The caller must already have enforced MaxJSONPatchBytes.
func ApplyJSONPatch(raw []byte, ops []PatchOp) ([]byte, error) {
	var doc any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, err
	}
	for _, op := range ops {
		switch strings.ToLower(strings.TrimSpace(op.Op)) {
		case "set":
			doc, _ = jsonPointerSet(doc, op.Path, op.Value)
		case "remove", "delete":
			doc, _ = jsonPointerRemove(doc, op.Path)
		}
	}
	return json.Marshal(doc)
}

var streamPool = sync.Pool{New: func() any { return make([]byte, streamBufferSize) }}

// StreamReplace copies src to dst, replacing every literal find with repl. It
// keeps at most len(find)-1 bytes of carry, so memory is O(buffer+find), not
// O(document). maxBytes bounds the input when src is not already limited.
func StreamReplace(dst io.Writer, src io.Reader, find, repl string, maxBytes int64) (int64, error) {
	if find == "" {
		if maxBytes > 0 {
			src = io.LimitReader(src, maxBytes)
		}
		buf := streamPool.Get().([]byte)
		defer streamPool.Put(buf)
		return io.CopyBuffer(dst, src, buf)
	}
	if maxBytes > 0 {
		src = io.LimitReader(src, maxBytes)
	}
	br := bufio.NewReaderSize(src, streamBufferSize)
	carry := make([]byte, 0, len(find)-1)
	var written int64
	for {
		chunk := streamPool.Get().([]byte)
		n, err := br.Read(chunk)
		if n > 0 {
			combined := make([]byte, 0, len(carry)+n)
			combined = append(combined, carry...)
			combined = append(combined, chunk[:n]...)
			cut := len(combined)
			if last := bytes.LastIndex(combined, []byte(find)); last >= 0 {
				cut = last + len(find)
			} else if keep := len(find) - 1; cut > keep {
				cut -= keep
			} else {
				cut = 0
			}
			out := bytes.ReplaceAll(combined[:cut], []byte(find), []byte(repl))
			if len(out) > 0 {
				m, werr := dst.Write(out)
				written += int64(m)
				if werr != nil {
					streamPool.Put(chunk)
					return written, werr
				}
			}
			carry = append(carry[:0], combined[cut:]...)
		}
		streamPool.Put(chunk)
		if err == io.EOF {
			break
		}
		if err != nil {
			return written, err
		}
	}
	if len(carry) > 0 {
		out := bytes.ReplaceAll(carry, []byte(find), []byte(repl))
		m, err := dst.Write(out)
		written += int64(m)
		if err != nil {
			return written, err
		}
	}
	return written, nil
}

func jsonPointerTokens(pointer string) []string {
	pointer = strings.TrimPrefix(pointer, "/")
	if pointer == "" {
		return nil
	}
	parts := strings.Split(pointer, "/")
	for i, s := range parts {
		parts[i] = strings.ReplaceAll(strings.ReplaceAll(s, "~1", "/"), "~0", "~")
	}
	return parts
}

func jsonPointerSet(doc any, pointer string, value any) (any, bool) {
	tokens := jsonPointerTokens(pointer)
	if len(tokens) == 0 {
		return value, true
	}
	return pointerSetWalk(doc, tokens, value, 0)
}

func pointerSetWalk(node any, tokens []string, value any, idx int) (any, bool) {
	tok, last := tokens[idx], idx == len(tokens)-1
	switch cur := node.(type) {
	case map[string]any:
		if last {
			cur[tok] = value
			return cur, true
		}
		child := cur[tok]
		if child == nil {
			child = map[string]any{}
		}
		v, ok := pointerSetWalk(child, tokens, value, idx+1)
		if ok {
			cur[tok] = v
		}
		return cur, ok
	case []any:
		if last && tok == "-" {
			return append(cur, value), true
		}
		n, err := strconv.Atoi(tok)
		if err != nil || n < 0 || n >= len(cur) {
			return cur, false
		}
		if last {
			cur[n] = value
			return cur, true
		}
		v, ok := pointerSetWalk(cur[n], tokens, value, idx+1)
		if ok {
			cur[n] = v
		}
		return cur, ok
	}
	return node, false
}

func jsonPointerRemove(doc any, pointer string) (any, bool) {
	tokens := jsonPointerTokens(pointer)
	if len(tokens) == 0 {
		return doc, false
	}
	return pointerRemoveWalk(doc, tokens, 0)
}

func pointerRemoveWalk(node any, tokens []string, idx int) (any, bool) {
	tok, last := tokens[idx], idx == len(tokens)-1
	switch cur := node.(type) {
	case map[string]any:
		child, ok := cur[tok]
		if !ok {
			return cur, false
		}
		if last {
			delete(cur, tok)
			return cur, true
		}
		v, ok := pointerRemoveWalk(child, tokens, idx+1)
		if ok {
			cur[tok] = v
		}
		return cur, ok
	case []any:
		n, err := strconv.Atoi(tok)
		if err != nil || n < 0 || n >= len(cur) {
			return cur, false
		}
		if last {
			return slices.Delete(cur, n, n+1), true
		}
		v, ok := pointerRemoveWalk(cur[n], tokens, idx+1)
		if ok {
			cur[n] = v
		}
		return cur, ok
	}
	return node, false
}

// Validate is used by tests and by the loader to report a rule that cannot do
// anything. It does not reject a regex that failed to compile; the loader
// already left that slot nil.
func (r Rule) Validate() error {
	switch r.Action {
	case "block":
		return nil
	case "override":
		if strings.TrimSpace(r.File) == "" {
			return fmt.Errorf("override rule needs file")
		}
		return nil
	case "patch":
		if len(r.Patch) == 0 {
			return fmt.Errorf("patch rule needs patch ops")
		}
		return nil
	case "replace":
		if r.Find == "" {
			return fmt.Errorf("replace rule needs find")
		}
		return nil
	default:
		return fmt.Errorf("unknown action %q", r.Action)
	}
}
