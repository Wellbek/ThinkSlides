package handler

import "testing"

func TestNormalizeMode(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"deep is the only opt-in", ModeDeep, ModeDeep},
		{"simple passes through", ModeSimple, ModeSimple},
		{"empty (old client) defaults to simple", "", ModeSimple},
		{"unknown value defaults to simple", "turbo", ModeSimple},
		{"matching is case-sensitive", "Deep", ModeSimple},
		{"whitespace is not trimmed away into deep", " deep", ModeSimple},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := NormalizeMode(tc.in); got != tc.want {
				t.Errorf("NormalizeMode(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}
