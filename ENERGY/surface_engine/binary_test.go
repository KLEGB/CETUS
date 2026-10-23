package main

import (
	"bytes"
	"math"
	"reflect"
	"testing"
)

func TestBinaryPreparedImmutableAndLevels(t *testing.T) {
	p := example()
	p.Hours[0].Irr = []float64{600, 600, 0}
	p.Hours[0].Internal = 200
	topology, n := makeTopology(p)
	p.Initial = make([]float64, n)
	for i := range p.Initial {
		p.Initial[i] = 20
	}
	old := append([]float64(nil), p.Initial...)
	want, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	s := binarySession{models: []prepared{{p: p, t: topology, n: n}}, step: []Input{p}, stepID: 7}
	for repeat := 0; repeat < 10; repeat++ {
		for level := 0; level < 3; level++ {
			b := new(bytes.Buffer)
			putID(b, 7)
			putU(b, uint32(level))
			putU(b, 1)
			putU(b, 0)
			putU(b, 0)
			raw, e := s.handle(3, b.Bytes())
			if e != nil {
				t.Fatal(e)
			}
			r := &reader{b: raw}
			if r.u() != 1 {
				t.Fatal("model count")
			}
			h := want.Hours[0]
			expected := []float64{h.Convection + h.Ventilation + h.OutdoorHVAC, h.GroundLW + h.GroundContact, math.Abs(h.Residual)}
			for _, v := range expected {
				if r.f() != v {
					t.Fatal("summary differs")
				}
			}
			if level == 1 {
				count := int(r.u())
				if count != len(p.Surfaces) {
					t.Fatal("surface count")
				}
				for _, v := range h.Surfaces {
					if r.f() != v.Te {
						t.Fatal("surface temperature differs")
					}
				}
			}
			if level == 2 {
				expected := new(bytes.Buffer)
				writeFull(expected, h)
				if !bytes.Equal(r.take(expected.Len()), expected.Bytes()) {
					t.Fatal("full output differs")
				}
			}
			if !reflect.DeepEqual(r.array(), want.Final) {
				t.Fatal("state differs")
			}
			if r.err != nil || r.p != len(raw) {
				t.Fatal("invalid response")
			}
		}
	}
	if !reflect.DeepEqual(p.Initial, old) {
		t.Fatal("trial changed old state")
	}
}

func TestBinaryRejectMalformedAndStale(t *testing.T) {
	s := new(binarySession)
	for op := uint16(1); op <= 4; op++ {
		if _, e := s.handle(op, []byte{1, 2}); e == nil {
			t.Fatalf("accepted truncated op %d", op)
		}
	}
	b := new(bytes.Buffer)
	putID(b, 1)
	putU(b, 0)
	if _, e := s.handle(2, b.Bytes()); e == nil {
		t.Fatal("begin without configured models")
	}
}

func TestBinaryConcurrentWorkspaces(t *testing.T) {
	p := example()
	topology, n := makeTopology(p)
	p.Initial = make([]float64, n)
	for i := range p.Initial {
		p.Initial[i] = 20
	}
	s := binarySession{models: make([]prepared, 16), step: make([]Input, 16), stepID: 9}
	for i := range s.models {
		s.models[i] = prepared{p: p, t: topology, n: n}
		s.step[i] = p
	}
	b := new(bytes.Buffer)
	putID(b, 9)
	putU(b, 0)
	putU(b, 0)
	putU(b, 0)
	putU(b, 0)
	for i := 0; i < 20; i++ {
		if _, e := s.handle(3, b.Bytes()); e != nil {
			t.Fatal(e)
		}
	}
}

func TestCandidateCacheUpgradeAndWarmStart(t *testing.T) {
	p := example()
	topology, n := makeTopology(p)
	p.Initial = make([]float64, n)
	for i := range p.Initial {
		p.Initial[i] = 20
	}
	old := append([]float64(nil), p.Initial...)
	s := binarySession{models: []prepared{{p: p, t: topology, n: n}}, step: []Input{p}, stepID: 10, warmStart: true, cacheEnabled: true}
	b := new(bytes.Buffer)
	putID(b, 10)
	putU(b, 0)
	putU(b, 0)
	putU(b, 0)
	putU(b, 1)
	putF(b, 20.0001)
	if _, e := s.handle(7, b.Bytes()); e != nil {
		t.Fatal(e)
	}
	solves := s.models[0].solves
	candidate := s.candidate
	get := new(bytes.Buffer)
	putID(get, 10)
	putID(get, candidate)
	putU(get, 2)
	putU(get, 1)
	putU(get, 0)
	if _, e := s.handle(6, get.Bytes()); e != nil {
		t.Fatal(e)
	}
	if s.models[0].solves != solves {
		t.Fatal("upgrade solved nodes again")
	}
	if _, e := s.handle(7, b.Bytes()); e != nil {
		t.Fatal(e)
	}
	if s.models[0].hits != 1 {
		t.Fatal("same hour was not cached")
	}
	if _, e := s.handle(6, get.Bytes()); e == nil {
		t.Fatal("old candidate accepted")
	}
	next := new(bytes.Buffer)
	putID(next, 10)
	putU(next, 0)
	putU(next, 0)
	putU(next, 0)
	putU(next, 1)
	putF(next, 20.0002)
	if _, e := s.handle(7, next.Bytes()); e != nil {
		t.Fatal(e)
	}
	if s.models[0].w.warmStarts == 0 {
		t.Fatal("warm initial guess unused")
	}
	if !reflect.DeepEqual(old, p.Initial) {
		t.Fatal("physical old state changed")
	}
}
