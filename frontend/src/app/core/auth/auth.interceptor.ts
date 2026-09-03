import { HttpErrorResponse, HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, from, switchMap, throwError } from 'rxjs';
import { UserService } from './user.service';

const GRAPH_URL = 'https://graph.microsoft.com';
const AUTH_ENDPOINTS = ['/api/v0/auth/login', '/api/v0/auth/refresh'];

function withBearer(req: HttpRequest<unknown>, token: string | null): HttpRequest<unknown> {
  return token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const userService = inject(UserService);

  // Microsoft Graph (e.g. the profile photo) needs the Microsoft id_token,
  // never the app access token.
  if (req.url.startsWith(GRAPH_URL)) {
    return from(userService.getValidMicrosoftIdToken()).pipe(
      switchMap((token) => next(withBearer(req, token)))
    );
  }

  const isApiRequest = req.url.startsWith('/api/');
  const isAuthEndpoint = AUTH_ENDPOINTS.some((path) => req.url.startsWith(path));

  // Only backend API calls carry the app token. Skip Easy Auth (/.auth/me),
  // static assets, and the login/refresh endpoints (which would recurse).
  if (!isApiRequest || isAuthEndpoint) {
    return next(req);
  }

  return from(userService.getValidAccessToken()).pipe(
    switchMap((token) =>
      next(withBearer(req, token)).pipe(
        catchError((error) => {
          if (error instanceof HttpErrorResponse && error.status === 401) {
            return from(userService.refreshAccessToken()).pipe(
              switchMap((refreshed) =>
                refreshed ? next(withBearer(req, refreshed)) : throwError(() => error)
              )
            );
          }
          return throwError(() => error);
        })
      )
    )
  );
};
