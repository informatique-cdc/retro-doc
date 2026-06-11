import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./features/dashboard/dashboard').then((m) => m.Dashboard),
  },
  {
    path: 'import',
    loadComponent: () =>
      import('./features/import/import-project').then((m) => m.ImportProject),
  },
  {
    path: 'project/:id',
    loadComponent: () => import('./features/project/project').then((m) => m.Project),
  },
  {
    path: 'project/:id/analysis',
    loadComponent: () => import('./features/analysis/analysis').then((m) => m.Analysis),
  },
  {
    path: 'project/:id/analysis/chat/:chatId',
    loadComponent: () => import('./features/analysis/analysis').then((m) => m.Analysis),
  },
  {
    path: 'project/:id/analysis/deep_analysis/:analysisId',
    loadComponent: () => import('./features/analysis/analysis').then((m) => m.Analysis),
  },
  {
    path: 'design',
    loadComponent: () => import('./features/design/design').then((m) => m.Design),
  },
];
