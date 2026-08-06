package handler

// Effort levels the composer can request for a message.
//
// ModeSimple answers from the configured chat model alone. ModeDeep enables
// the web-search-backed research path, which the AI service will itself
// downgrade to simple if the organisation has no deep-search backend
// configured - so the orchestrator never has to know about that configuration.
const (
	ModeSimple = "simple"
	ModeDeep   = "deep"
)

// NormalizeMode maps any client-supplied value onto a known mode.
//
// Unrecognised values (including the empty string an older client sends) fall
// back to simple rather than erroring, so adding the field to the API does not
// break clients that predate it.
func NormalizeMode(mode string) string {
	if mode == ModeDeep {
		return ModeDeep
	}
	return ModeSimple
}
