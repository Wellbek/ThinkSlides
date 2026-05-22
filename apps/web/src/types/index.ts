export type {
  ChatMessage as MessageItem,
  ChatSession as SessionListItem,
  Template as TemplateItem,
  GeneratedFile,
} from "@/stores/chat-store";

// If you actually need an Attachment type, define it separately or remove it.
// Example:
// export type AttachmentItem = { url: string; type: string; name: string };