export type Language = 'java' | 'python' | 'typescript' | 'cobol';

export interface Repo {
  repo_id: string;
  name: string;
  repo_url: string | null;
  repo_branch: string | null;
  repo_hash: string | null;
  language: Language;
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepoListResponse {
  repos: Repo[];
}

export interface RepoDetail extends Repo {
  content: string | null;
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
  role: ChatRole;
  content: string;
  timestamp?: Date;
  context?: { fileName: string; nodeLabel: string };
  reasoning?: ChatMessageSegment[];
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
  role: string;
  content: string;
}

export interface ChatThreadMessagesResponse {
  chat_id: string;
  messages: ChatMessageResponse[];
}

export type ChatStreamEvent =
  | { type: 'chat_id'; chatId: string }
  | { type: 'token'; content: string }
  | { type: 'tool_start'; tool: string; id: string }
  | { type: 'tool_end'; tool: string; id: string; status: ToolStatus };
