import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class AnalysisActionService {
  readonly restart$ = new Subject<void>();

  triggerRestart(): void {
    this.restart$.next();
  }
}
