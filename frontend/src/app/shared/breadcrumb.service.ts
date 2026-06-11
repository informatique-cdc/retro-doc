import { Injectable, signal } from '@angular/core';

export interface BreadcrumbItem {
  label: string;
  route?: string;
}

@Injectable({ providedIn: 'root' })
export class BreadcrumbService {
  readonly items = signal<BreadcrumbItem[]>([]);

  set(items: BreadcrumbItem[]): void {
    this.items.set(items);
  }
}
