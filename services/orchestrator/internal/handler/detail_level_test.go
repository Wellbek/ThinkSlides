package handler

import "testing"

func TestNormalizeDetailLevel(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"concise passes through", DetailLevelConcise, DetailLevelConcise},
		{"detailed passes through", DetailLevelDetailed, DetailLevelDetailed},
		{"comprehensive passes through", DetailLevelComprehensive, DetailLevelComprehensive},
		{"empty (old client) defaults to detailed", "", DetailLevelDetailed},
		{"unknown value defaults to detailed", "extreme", DetailLevelDetailed},
		{"matching is case-sensitive", "Concise", DetailLevelDetailed},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := NormalizeDetailLevel(tc.in); got != tc.want {
				t.Errorf("NormalizeDetailLevel(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}
