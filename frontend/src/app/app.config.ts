import {
  APP_INITIALIZER,
  ApplicationConfig,
  inject,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideTranslateService } from '@ngx-translate/core';
import { provideTranslateHttpLoader } from '@ngx-translate/http-loader';

import { routes } from './app.routes';
import { authInterceptor, UserService } from './core/auth';
import { LanguageService } from './core/i18n';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideTranslateService({
      fallbackLang: 'fr',
      lang: 'fr',
      loader: provideTranslateHttpLoader({ prefix: './i18n/', suffix: '.json' }),
    }),
    {
      provide: APP_INITIALIZER,
      useFactory: () => {
        const userService = inject(UserService);
        return () => userService.loadUser();
      },
      multi: true,
    },
    {
      provide: APP_INITIALIZER,
      useFactory: () => {
        const languageService = inject(LanguageService);
        return () => languageService.init();
      },
      multi: true,
    },
  ],
};
