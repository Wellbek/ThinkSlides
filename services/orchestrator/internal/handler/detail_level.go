package handler

// Content depth levels the composer can request for a generation.
//
// DetailLevelConcise keeps short, single-sentence bullets. DetailLevelDetailed
// (the default) asks for fuller bullets with supporting detail.
// DetailLevelComprehensive goes further still. The AI service is what
// actually varies the generation prompts by this value - the orchestrator
// only validates and forwards it.
const (
	DetailLevelConcise       = "concise"
	DetailLevelDetailed      = "detailed"
	DetailLevelComprehensive = "comprehensive"
)

// NormalizeDetailLevel maps any client-supplied value onto a known level.
//
// Unrecognised values (including the empty string an older client sends)
// fall back to detailed rather than erroring, so adding the field to the API
// does not break clients that predate it - and detailed is a better default
// than the old de-facto concise-only output.
func NormalizeDetailLevel(level string) string {
	switch level {
	case DetailLevelConcise, DetailLevelComprehensive:
		return level
	default:
		return DetailLevelDetailed
	}
}
