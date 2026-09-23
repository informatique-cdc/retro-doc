export type Language = 'java' | 'python' | 'typescript' | 'cobol';

export interface SupportedLanguagesResponse {
  languages: string[];
}

export interface Repo {
  repo_id: string;
  name: string;
  repo_url: string;
  /**
   * The commit a git source was pinned to, and the discriminator between the
   * two kinds of repository: a zip upload leaves it null.
   */
  repo_hash: string | null;
  languages: Language[];
  /** Analyzer that produced the documentation. Null until a run has stamped one. */
  analyzer_version: string | null;
  /** Whether a newer analyzer exists, i.e. whether relaunching would add anything. */
  stale: boolean;
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

/** Why a run is where it is: the step it reached and what happened there. */
export interface PipelineMeta {
  message: string;
  step: string;
}

/**
 * One attempt at analyzing a repository.
 *
 * `retried_at` is set on the attempts a retry superseded, so a failure that was
 * restarted can be told apart from one that still stands.
 */
export interface PipelineAttempt {
  status: PipelineStatus;
  started_at: string;
  finished_at: string | null;
  retried_at: string | null;
  meta: PipelineMeta | null;
}

export interface PipelineStatusResponse {
  repo_id: string;
  /** The latest attempt's status — the same one as `attempts[0]`. */
  status: PipelineStatus;
  meta: PipelineMeta | null;
  /** Latest attempt first. Never empty: a repository with no runs is a 404. */
  attempts: PipelineAttempt[];
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

/**
 * A git source to analyze.
 *
 * No `languages`: a git analysis is shared between everyone who asks for the
 * same commit, so it always covers every supported language, and the backend
 * rejects a language filter here.
 */
export interface AnalyzeGitRequest {
  repo_url: string;
  name: string;
  color: string;
  /** Branch to take the commit from. Defaults to the remote's default branch. */
  branch?: string;
  /** Explicit commit SHA. Wins over `branch` when both are given. */
  commit?: string;
  /** Credential for a private remote. Used to reach it, never stored. */
  token?: string;
}

export interface AnalyzeGitResult extends AnalyzeFileResponse {
  /**
   * Whether an analysis of this commit already existed and was joined rather
   * than started — the backend's 200, against 202 for one this call started.
   */
  joined: boolean;
}

export interface ImportRepoResponse {
  repo_id: string;
  name: string;
}

export interface RelaunchRepoRequest {
  /**
   * Credential for a private git remote. Relaunching fetches the remote again,
   * so unlike joining it takes access. Used to reach it, never stored.
   */
  token?: string;
}

export interface RelaunchRepoResponse {
  /**
   * Where the relaunched analysis lives: a new repository when it moved to a
   * newer analyzer version, the one relaunched when a failed run was retried in
   * place. Compare against the id sent to tell the two apart — nothing is
   * replaced either way.
   */
  repo_id: string;
  status: PipelineStatus;
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
