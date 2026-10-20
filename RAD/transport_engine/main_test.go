package main

import "testing"

func TestFiniteSegmentAndBVH(t *testing.T) {
	tri := Tri{V{0, -10, 0}, V{0, 10, 0}, V{0, 0, 10}}
	tree := build([]Tri{tri})
	o, d := V{-1, 0, 2}, V{1, 0, 0}
	if blocked(tree, o, d, .5) || !blocked(tree, o, d, 2) || blocked(tree, o, V{-1, 0, 0}, 2) {
		t.Fatal("finite visibility")
	}
}
