import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { map, Observable } from 'rxjs';
import { DeepAnalysis, DeepAnalysisDetail, DeepAnalysisListResponse } from './api.models';

@Injectable({ providedIn: 'root' })
export class DeepAnalysisService {
  private readonly http = inject(HttpClient);

  createAnalysis(repoId: string, query: string): Observable<DeepAnalysis> {
    return this.http.post<DeepAnalysis>('/api/v0/deep-analysis', { repo_id: repoId, query });
  }

  listAnalyses(repoId?: string): Observable<DeepAnalysis[]> {
    let params = new HttpParams();
    if (repoId) {
      params = params.set('repo_id', repoId);
    }
    return this.http
      .get<DeepAnalysisListResponse>('/api/v0/deep-analysis', { params })
      .pipe(map((res) => res.analyses));
  }

  getAnalysis(id: string): Observable<DeepAnalysisDetail> {
    return this.http.get<DeepAnalysisDetail>(
      `/api/v0/deep-analysis/${encodeURIComponent(id)}`
    );
  }

  deleteAnalysis(id: string): Observable<void> {
    return this.http.delete<void>(
      `/api/v0/deep-analysis/${encodeURIComponent(id)}`
    );
  }

  downloadPdf(id: string): void {
    this.http
      .get(`/api/v0/deep-analysis/${encodeURIComponent(id)}/pdf`, {
        responseType: 'blob',
      })
      .subscribe((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `deep-analysis-${id}.pdf`;
        a.click();
        URL.revokeObjectURL(url);
      });
  }
}
