import { inject, Injectable } from '@angular/core';
import { BehaviorSubject, Observable, shareReplay, switchMap } from 'rxjs';
import { Repo, RepoDetail, RepoFile } from './api.models';
import { RepoService } from './repo.service';

@Injectable({ providedIn: 'root' })
export class RepoStore {
  private readonly repoService = inject(RepoService);

  private readonly reposRefresh$ = new BehaviorSubject<void>(undefined);
  private repoDetailCache = new Map<string, Observable<RepoDetail>>();
  private repoFilesCache = new Map<string, Observable<RepoFile[]>>();

  private readonly repos$ = this.reposRefresh$.pipe(
    switchMap(() => this.repoService.getRepos()),
    shareReplay(1)
  );

  /** Emits whenever the repos list is invalidated. Use in combineLatest to re-trigger queries. */
  readonly reposInvalidated$ = this.reposRefresh$.asObservable();

  getRepos(): Observable<Repo[]> {
    return this.repos$;
  }

  getRepo(id: string): Observable<RepoDetail> {
    let cached = this.repoDetailCache.get(id);
    if (!cached) {
      cached = this.repoService.getRepo(id).pipe(shareReplay(1));
      this.repoDetailCache.set(id, cached);
    }
    return cached;
  }

  getRepoFiles(id: string): Observable<RepoFile[]> {
    let cached = this.repoFilesCache.get(id);
    if (!cached) {
      cached = this.repoService.getRepoFiles(id).pipe(shareReplay(1));
      this.repoFilesCache.set(id, cached);
    }
    return cached;
  }

  invalidateRepos(): void {
    this.reposRefresh$.next();
  }

  invalidateRepo(id: string): void {
    this.repoDetailCache.delete(id);
    this.repoFilesCache.delete(id);
    this.reposRefresh$.next();
  }
}
