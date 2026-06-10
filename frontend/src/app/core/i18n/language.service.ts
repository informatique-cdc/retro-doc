import { computed, inject, Injectable, signal } from '@angular/core';
import { TranslateService } from '@ngx-translate/core';

export type AppLanguage = 'fr' | 'en';

const STORAGE_KEY = 'app-lang';
const DEFAULT_LANG: AppLanguage = 'fr';

@Injectable({ providedIn: 'root' })
export class LanguageService {
  private readonly translate = inject(TranslateService);

  readonly currentLang = signal<AppLanguage>(this.readStoredLang());
  readonly isFrench = computed(() => this.currentLang() === 'fr');

  init(): Promise<void> {
    const lang = this.currentLang();
    this.translate.setDefaultLang(DEFAULT_LANG);
    document.documentElement.lang = lang;
    return new Promise((resolve) => {
      this.translate.use(lang).subscribe(() => resolve());
    });
  }

  setLanguage(lang: AppLanguage): void {
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang;
    this.translate.use(lang).subscribe(() => {
      this.currentLang.set(lang);
    });
  }

  private readStoredLang(): AppLanguage {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'fr' || stored === 'en') {
      return stored;
    }
    return DEFAULT_LANG;
  }
}
