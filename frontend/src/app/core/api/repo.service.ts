import { inject, Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { map, Observable } from 'rxjs';
import {
  AnalyzeFileResponse,
  AnalyzeGitRequest,
  AnalyzeGitResult,
  FileDocumentationResponse,
  FileGraphsResponse,
  FileSourceResponse,
  ImportRepoResponse,
  PipelineStatusResponse,
  RelaunchRepoRequest,
  RelaunchRepoResponse,
  Repo,
  RepoDetail,
  RepoFile,
  RepoFilesResponse,
  RepoListResponse,
  SupportedLanguagesResponse,
  UpdateUserRepoRequest,
} from './api.models';

@Injectable({ providedIn: 'root' })
export class RepoService {
  private readonly http = inject(HttpClient);

  getSupportedLanguages(): Observable<string[]> {
    return this.http
      .get<SupportedLanguagesResponse>('/api/v0/languages')
      .pipe(map((res) => res.languages));
  }

  getRepos(search?: string): Observable<Repo[]> {
    let params = new HttpParams();
    if (search) {
      params = params.set('search', search);
    }
    return this.http.get<RepoListResponse>('/api/v0/repos', { params }).pipe(map((res) => res.repos));
  }

  getRepo(id: string): Observable<RepoDetail> {
    return this.http.get<RepoDetail>(`/api/v0/repos/${encodeURIComponent(id)}`);
  }

  getRepoFiles(id: string): Observable<RepoFile[]> {
    return this.http
      .get<RepoFilesResponse>(`/api/v0/repos/${encodeURIComponent(id)}/files`)
      .pipe(map((res) => res.files));
  }

  getPipelineStatus(id: string): Observable<PipelineStatusResponse> {
    return this.http.get<PipelineStatusResponse>(
      `/api/v0/repos/${encodeURIComponent(id)}/pipeline`
    );
  }

  getFileGraphs(repoId: string, fileId: string): Observable<FileGraphsResponse> {
    return this.http.get<FileGraphsResponse>(
      `/api/v0/repos/${encodeURIComponent(repoId)}/files/${encodeURIComponent(fileId)}/graphs`
    );
  }

  getFileSource(repoId: string, fileId: string): Observable<FileSourceResponse> {
    return this.http.get<FileSourceResponse>(
      `/api/v0/repos/${encodeURIComponent(repoId)}/files/${encodeURIComponent(fileId)}/src`
    );
  }

  getFileDoc(repoId: string, fileId: string): Observable<FileDocumentationResponse> {
    return this.http.get<FileDocumentationResponse>(
      `/api/v0/repos/${encodeURIComponent(repoId)}/files/${encodeURIComponent(fileId)}/doc`
    );
  }

  analyzeFile(
    file: File,
    name: string,
    languages: string[],
    color: string
  ): Observable<AnalyzeFileResponse> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name);
    for (const language of languages) {
      formData.append('languages', language);
    }
    formData.append('color', color);
    return this.http.post<AnalyzeFileResponse>('/api/v0/repos', formData);
  }

  importRepo(repoId: string): Observable<ImportRepoResponse> {
    return this.http.post<ImportRepoResponse>(
      `/api/v0/repos/${encodeURIComponent(repoId)}/join`,
      null
    );
  }

  /**
   * Re-analyze a repository at the worker's current analyzer version.
   *
   * Also how a failed run is retried — the backend has no separate endpoint for
   * it, and which of the two happens is decided there from the repository's own
   * state. The response says which: a new `repo_id` is an analysis at a newer
   * version to move to, the one sent is a retry that stayed in place.
   *
   * `token` reaches a private git remote, and is omitted rather than sent empty
   * so a public one is not handed a blank credential.
   */
  relaunchRepo(repoId: string, token?: string): Observable<RelaunchRepoResponse> {
    const request: RelaunchRepoRequest = token ? { token } : {};
    return this.http.post<RelaunchRepoResponse>(
      `/api/v0/repos/${encodeURIComponent(repoId)}/relaunch`,
      request
    );
  }

  updateUserRepo(repoId: string, request: UpdateUserRepoRequest): Observable<Repo> {
    return this.http.patch<Repo>(
      `/api/v0/repos/${encodeURIComponent(repoId)}`,
      request
    );
  }

  deleteRepo(repoId: string): Observable<void> {
    return this.http.delete<void>(`/api/v0/repos/${encodeURIComponent(repoId)}`);
  }

  /**
   * Start an analysis of a git repository, or join one that already exists.
   *
   * Shares the zip upload's endpoint: the backend takes the git path from
   * `repo_url` being set in place of `file`. Optional fields are omitted rather
   * than sent empty, so the backend applies its own defaults — the remote's
   * default branch, and no credential — instead of resolving a blank branch.
   */
  analyzeGit(request: AnalyzeGitRequest): Observable<AnalyzeGitResult> {
    const formData = new FormData();
    formData.append('repo_url', request.repo_url);
    formData.append('name', request.name);
    formData.append('color', request.color);
    if (request.branch) {
      formData.append('branch', request.branch);
    }
    if (request.commit) {
      formData.append('commit', request.commit);
    }
    if (request.token) {
      formData.append('token', request.token);
    }

    return this.http
      .post<AnalyzeFileResponse>('/api/v0/repos', formData, { observe: 'response' })
      .pipe(map((res) => ({ ...res.body!, joined: res.status === 200 })));
  }
}
