// Persistent BVH visibility service. Geometry JSON, binary ray batches on stdin.
// Each ray: origin XYZ, direction XYZ, maximum distance (7 little-endian float64).
// Batch count uint32; output one byte per ray (1 visible, 0 blocked).
package main

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"runtime"
	"sort"
	"sync"
)

type V [3]float64
type Tri [3]V
type Node struct {
	Lo, Hi      V
	Left, Right *Node
	Faces       []Tri
}

func sub(a, b V) V       { return V{a[0] - b[0], a[1] - b[1], a[2] - b[2]} }
func dot(a, b V) float64 { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2] }
func cross(a, b V) V     { return V{a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0]} }
func build(t []Tri) *Node {
	n := &Node{Lo: V{math.Inf(1), math.Inf(1), math.Inf(1)}, Hi: V{math.Inf(-1), math.Inf(-1), math.Inf(-1)}}
	for _, f := range t {
		for _, p := range f {
			for k := 0; k < 3; k++ {
				n.Lo[k] = math.Min(n.Lo[k], p[k])
				n.Hi[k] = math.Max(n.Hi[k], p[k])
			}
		}
	}
	if len(t) <= 8 {
		n.Faces = t
		return n
	}
	axis := 0
	for k := 1; k < 3; k++ {
		if n.Hi[k]-n.Lo[k] > n.Hi[axis]-n.Lo[axis] {
			axis = k
		}
	}
	sort.Slice(t, func(i, j int) bool {
		return t[i][0][axis]+t[i][1][axis]+t[i][2][axis] < t[j][0][axis]+t[j][1][axis]+t[j][2][axis]
	})
	mid := len(t) / 2
	n.Left = build(t[:mid])
	n.Right = build(t[mid:])
	return n
}
func hitTri(t Tri, o, d V, limit float64) bool {
	a := sub(t[1], t[0])
	b := sub(t[2], t[0])
	p := cross(d, b)
	det := dot(a, p)
	if math.Abs(det) < 1e-10 {
		return false
	}
	q := sub(o, t[0])
	u := dot(q, p) / det
	if u < -1e-9 || u > 1+1e-9 {
		return false
	}
	v := dot(d, cross(q, a)) / det
	if v < -1e-9 || u+v > 1+1e-9 {
		return false
	}
	distance := dot(b, cross(q, a)) / det
	return distance > 1e-4 && distance < limit-1e-4
}
func blocked(n *Node, o, d V, limit float64) bool {
	if n == nil {
		return false
	}
	lo, hi := 0., limit
	for k := 0; k < 3; k++ {
		if math.Abs(d[k]) < 1e-14 {
			if o[k] < n.Lo[k] || o[k] > n.Hi[k] {
				return false
			}
			continue
		}
		a, b := (n.Lo[k]-o[k])/d[k], (n.Hi[k]-o[k])/d[k]
		if a > b {
			a, b = b, a
		}
		lo = math.Max(lo, a)
		hi = math.Min(hi, b)
		if lo > hi {
			return false
		}
	}
	if n.Left != nil {
		return blocked(n.Left, o, d, limit) || blocked(n.Right, o, d, limit)
	}
	for _, t := range n.Faces {
		if hitTri(t, o, d, limit) {
			return true
		}
	}
	return false
}
func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("expected scene JSON path")
	}
	raw, e := os.ReadFile(os.Args[1])
	if e != nil {
		return e
	}
	var triangles []Tri
	if e = json.Unmarshal(raw, &triangles); e != nil {
		return e
	}
	tree := build(triangles)
	for {
		var count uint32
		if e = binary.Read(os.Stdin, binary.LittleEndian, &count); e == io.EOF {
			return nil
		} else if e != nil {
			return e
		}
		if count > 1000000 {
			return fmt.Errorf("batch too large")
		}
		rays := make([][7]float64, count)
		if e = binary.Read(os.Stdin, binary.LittleEndian, &rays); e != nil {
			return e
		}
		out := make([]byte, count)
		jobs := make(chan int, len(rays))
		var wg sync.WaitGroup
		workers := runtime.GOMAXPROCS(0)
		for k := 0; k < workers; k++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for i := range jobs {
					r := rays[i]
					o, d := V{r[0], r[1], r[2]}, V{r[3], r[4], r[5]}
					if !blocked(tree, o, d, r[6]) {
						out[i] = 1
					}
				}
			}()
		}
		for i := range rays {
			jobs <- i
		}
		close(jobs)
		wg.Wait()
		if _, e = os.Stdout.Write(out); e != nil {
			return e
		}
	}
}
func main() {
	if e := run(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
