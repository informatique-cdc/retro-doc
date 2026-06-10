import { inject, Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { map, Observable } from 'rxjs';
import {
  AnalyzeFileResponse,
  FileGraphsResponse,
  ImportRepoResponse,
  Language,
  PipelineStatusResponse,
  Repo,
  RepoDetail,
  RepoFile,
  RepoFilesResponse,
  RepoListResponse,
  UpdateUserRepoRequest,
} from './api.models';

@Injectable({ providedIn: 'root' })
export class RepoService {
  private readonly http = inject(HttpClient);

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

  analyzeFile(file: File, name: string, language: Language, color: string): Observable<AnalyzeFileResponse> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name);
    formData.append('language', language);
    formData.append('color', color);
    return this.http.post<AnalyzeFileResponse>('/api/v0/repos', formData);
  }

  importRepo(repoId: string): Observable<ImportRepoResponse> {
    return this.http.post<ImportRepoResponse>(
      `/api/v0/repos/${encodeURIComponent(repoId)}/join`,
      null
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

  analyzeGitUrl(
    url: string,
    name: string,
    language: Language,
    color: string
  ): Observable<AnalyzeFileResponse> {
    return this.http.post<AnalyzeFileResponse>('/api/v0/repos/analyze/git', {
      url,
      name,
      language,
      color,
    });
  }
}
