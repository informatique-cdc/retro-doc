export type Language = 'java' | 'python' | 'typescript' | 'cobol';

export interface SupportedLanguagesResponse {
  languages: string[];
}

export interface Repo {
  repo_id: string;
  name: string;
  repo_url: string | null;
  repo_branch: string | null;
  repo_hash: string | null;
  languages: Language[];
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepoListResponse {
  repos: Repo[];
}

export interface AnalysisStats {
  files_detected: number;
  files_by_extension: Record<string, number>;
  file_success: number;
  file_failed: number;
  ast_success: number;
  ast_failed: number;
  cfg_success: number;
  cfg_failed: number;
  cfg_build_failed: number;
  dfg_success: number;
  dfg_failed: number;
  dfg_build_failed: number;
  doc_success: number;
  doc_failed: number;
  rag_success: number;
  rag_failed: number;
}

export interface RepoDetail extends Repo {
  content: string | null;
  stats: AnalysisStats | null;
}

export interface RepoFile {
  file_id: string;
  path: string;
  file_hash: string;
}

export interface RepoFilesResponse {
  repo_id: string;
  files: RepoFile[];
}

export interface FileSourceResponse {
  repo_id: string;
  file_id: string;
  path: string;
  content: string;
}

export interface FileDocumentationResponse {
  repo_id: string;
  file_id: string;
  content: string;
}

export type PipelineStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface PipelineStatusResponse {
  repo_id: string;
  status: PipelineStatus;
}

export interface ScopedGraph {
  scope: string | null;
  content: Record<string, unknown>;
}

export interface FileGraphsResponse {
  repo_id: string;
  file_id: string;
  ast: Record<string, unknown> | null;
  cfg: ScopedGraph[];
  dfg: ScopedGraph[];
}

export interface AnalyzeFileResponse {
  repo_id: string;
  status: PipelineStatus;
}

export interface ImportRepoResponse {
  repo_id: string;
  name: string;
}

export interface UpdateUserRepoRequest {
  name?: string | null;
  color?: string | null;
}

export type DeepAnalysisStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface DeepAnalysis {
  id: string;
  repo_id: string;
  query: string;
  status: DeepAnalysisStatus;
  created_at: string;
  finished_at: string | null;
}

export interface DeepAnalysisDetail extends DeepAnalysis {
  content: string | null;
  error: string | null;
}

export interface DeepAnalysisListResponse {
  analyses: DeepAnalysis[];
}

export type ChatRole = 'user' | 'assistant' | 'human' | 'ai';

export type ToolStatus = 'success' | 'error';

export interface ChatMessageSegment {
  type: 'text' | 'tool';
  content: string;
  toolId?: string;
  toolStatus?: ToolStatus;
}

export interface ChatMessage {
  /**
   * Stable identity for tracking and targeted updates, independent of array position.
   *
   * Never reassigned once a message is rendered: `@for` tracks by it, so
   * changing it tears the DOM node down and rebuilds it, losing the scroll
   * position and any text streaming into it.
   */
  key: string;
  /** The server's document ID, once the message has been persisted. */
  id?: string;
  role: ChatRole;
  content: string;
  timestamp?: Date;
  context?: { fileName: string; nodeLabel: string };
  reasoning?: ChatMessageSegment[];
  /** 1-based position among the answers to the same question. */
  variantIndex?: number;
  /** Total answers to the same question. Absent or 1 means no pager. */
  variantCount?: number;
  prevVariantId?: string;
  nextVariantId?: string;
}

export interface ChatThread {
  chat_id: string;
  repo_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatThreadListResponse {
  threads: ChatThread[];
}

export interface ChatMessageResponse {
  id: string;
  role: string;
  content: string;
  /** Pager fields, sent only for a question that has more than one answer. */
  variant_index?: number;
  variant_count?: number;
  prev_variant_id?: string;
  next_variant_id?: string;
}

export interface ChatThreadMessagesResponse {
  chat_id: string;
  messages: ChatMessageResponse[];
  /**
   * Pass as `before` to load the preceding page. Omitted on the last page,
   * which is also how the end of the history is signalled.
   */
  next_cursor?: string | null;
}

/** A file a tool referenced, so the answer can link back to it. */
export interface ChatSource {
  path: string;
  file_id: string;
}

/**
 * One event from a chat SSE stream.
 *
 * Every event type the server emits is represented here: an unmodelled type
 * would otherwise be misread as a token and its payload appended to the
 * message body.
 */
export type ChatStreamEvent =
  | { type: 'chat_id'; chatId: string }
  | { type: 'token'; content: string }
  | { type: 'tool_start'; tool: string; id: string }
  | { type: 'tool_end'; tool: string; id: string; status: ToolStatus; sources?: ChatSource[] }
  | { type: 'title'; title: string }
  | { type: 'error'; detail: string }
  | {
      type: 'message_saved';
      messageId: string;
      humanMessageId?: string;
      /** Defaulted to 1 when the server omits them, i.e. the only answer so far. */
      variantIndex: number;
      variantCount: number;
      prevVariantId?: string;
    };
