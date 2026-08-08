/**
 * Shared API request/response schemas
 */

export interface CreateSessionRequest {
  title?: string;
  templateId?: string;
  inlineTemplate?: File;
  documents?: File[];
  brief?: string;
}

export interface CreateSessionResponse {
  sessionId: string;
  title: string;
  createdAt: string;
}

/**
 * Effort level for a message.
 *
 * `simple` answers from the configured chat model alone; `deep` additionally
 * lets the agent research the topic with web search. The server treats any
 * unrecognised value - including the field being absent - as `simple`.
 */
export type EffortMode = 'simple' | 'deep';

/**
 * How much content depth the generated presentation should aim for.
 *
 * `concise` keeps today's terse, single-sentence bullets; `detailed` (the
 * default) raises the baseline to fuller bullets with supporting detail;
 * `comprehensive` goes further still, encouraging sub-bullets and richer
 * speaker notes. The server treats any unrecognised value - including the
 * field being absent - as `detailed`.
 */
export type DetailLevel = 'concise' | 'detailed' | 'comprehensive';

export interface SendMessageRequest {
  content: string;
  mode?: EffortMode;
  detailLevel?: DetailLevel;
  attachments?: Array<{
    fileName: string;
    mimeType: string;
    size: number;
  }>;
}

export interface SendMessageResponse {
  messageId: string;
  sessionId: string;
}

export interface SessionListItem {
  id: string;
  title: string;
  templateBadge?: string;
  slideCount: number;
  updatedAt: string;
  createdAt: string;
}

export interface SessionDetail {
  id: string;
  title: string;
  templateId?: string;
  templateBadge?: string;
  inlineTemplateFileName?: string;
  slideCount: number;
  messages: MessageItem[];
  events: AgentEventImported[];
  generatedFiles: GeneratedFileItem[];
  createdAt: string;
  updatedAt: string;
}

export interface MessageItem {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  attachments: AttachmentItem[];
  createdAt: string;
}

export interface AttachmentItem {
  fileName: string;
  mimeType: string;
  size: number;
  url: string;
}

export interface AgentEventImported {
  id: string;
  eventType: string;
  phase: string;
  content: unknown;
  toolName?: string;
  createdAt: string;
}

export interface GeneratedFileItem {
  id: string;
  fileType: string;
  fileName: string;
  fileSizeBytes: number;
  downloadUrl: string;
  createdAt: string;
}

export interface TemplateListItem {
  id: string;
  name: string;
  description?: string;
  tags: string[];
  version: number;
  isDefault: boolean;
  originalFileName: string;
  createdAt: string;
}

export interface TemplateDetail extends TemplateListItem {
  designSystemBundleId: string;
  layoutCount: number;
  palette: unknown;
  typography: unknown;
}

export interface AuthLoginRequest {
  email: string;
  password: string;
}

export interface AuthLoginResponse {
  token: string;
  user: {
    id: string;
    email: string;
    displayName: string;
    organisationId: string;
    role: string;
  };
}