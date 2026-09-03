import { computed, inject, Injectable, signal } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import {
  AuthMeResponse,
  AuthMeUserClaim,
  LoginRequest,
  RefreshRequest,
  TokenResponse,
  UserInfo,
} from './auth.models';

const CLAIM_TYPE_NAME = 'name';
const CLAIM_TYPE_EMAIL = 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress';
const CLAIM_TYPE_ROLES = 'http://schemas.microsoft.com/ws/2008/06/identity/claims/role';
const CLAIM_TYPE_ROLES_SHORT = 'roles';

const AUTH_ME_URL = '/.auth/me';
const EASY_AUTH_REFRESH_URL = '/.auth/refresh';
const EASY_AUTH_LOGIN_URL = '/.auth/login/aad';
const AUTH_LOGIN_URL = '/api/v0/auth/login/microsoft';
const AUTH_REFRESH_URL = '/api/v0/auth/refresh';
// Refresh a little before the real expiry to absorb clock skew / request latency.
const TOKEN_EXPIRY_SKEW_MS = 30_000;

@Injectable({ providedIn: 'root' })
export class UserService {
  private readonly http = inject(HttpClient);
  private readonly document = inject(DOCUMENT);

  private handlingExpiredToken = false;

  // Microsoft id_token from Easy Auth. Only used for Microsoft Graph calls
  // (e.g. profile photo); the backend API never accepts it.
  private microsoftIdToken: string | null = null;

  private readonly appAccessToken = signal<string | null>(null);
  private appRefreshToken: string | null = null;
  private accessTokenExpiresAt = 0;
  private refreshInFlight: Promise<void> | null = null;

  readonly user = signal<UserInfo | null>(null);
  readonly isAuthenticated = computed(() => this.user() !== null);
  readonly isLoading = signal(false);
  readonly tokenExpired = signal(false);

  async loadUser(): Promise<void> {
    this.isLoading.set(true);
    try {
      const response = await firstValueFrom(this.http.get<AuthMeResponse[]>(AUTH_ME_URL));
      if (response?.length) {
        const identity = response[0];
        this.microsoftIdToken = identity.id_token;
        this.user.set(this.mapToUserInfo(identity));
        await this.exchangeMicrosoftToken(identity.id_token);
      }
    } catch {
      this.user.set(null);
      this.microsoftIdToken = null;
      this.clearTokens();
    } finally {
      this.isLoading.set(false);
    }
  }

  getMicrosoftIdToken(): string | null {
    return this.microsoftIdToken;
  }

  // Non-expired Microsoft id_token for Graph; refreshes the Easy Auth store (needs offline_access) when near expiry.
  async getValidMicrosoftIdToken(): Promise<string | null> {
    const token = this.microsoftIdToken;
    if (!token) return null;
    if (Date.now() < this.idTokenExpiresAt(token) - TOKEN_EXPIRY_SKEW_MS) {
      return token;
    }
    return this.refreshMicrosoftIdToken();
  }

  private async refreshMicrosoftIdToken(): Promise<string | null> {
    try {
      // /.auth/refresh returns an empty body, so don't parse it as JSON.
      await firstValueFrom(this.http.get(EASY_AUTH_REFRESH_URL, { responseType: 'text' }));
      const response = await firstValueFrom(this.http.get<AuthMeResponse[]>(AUTH_ME_URL));
      this.microsoftIdToken = response?.[0]?.id_token ?? null;
    } catch {
      this.microsoftIdToken = null;
      this.redirectToEasyAuthLogin();
    }
    return this.microsoftIdToken;
  }

  private idTokenExpiresAt(idToken: string): number {
    try {
      const payload = idToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const decoded = JSON.parse(atob(payload)) as { exp?: number };
      return (decoded.exp ?? 0) * 1000;
    } catch {
      return 0;
    }
  }

  // No refresh token in the Easy Auth store (offline_access missing/expired): bounce through a full re-login.
  private redirectToEasyAuthLogin(): void {
    if (this.isLocalhost()) return;
    const view = this.document.defaultView;
    if (!view) return;
    const returnTo = encodeURIComponent(view.location.pathname + view.location.search);
    view.location.href = `${EASY_AUTH_LOGIN_URL}?post_login_redirect_uri=${returnTo}`;
  }

  // Returns a non-expired app access token for backend calls, refreshing first
  // when the current one is about to expire. The Microsoft id_token from Easy
  // Auth is only accepted by the login endpoint, never by the API.
  async getValidAccessToken(): Promise<string | null> {
    if (!this.appAccessToken()) return null;
    if (Date.now() >= this.accessTokenExpiresAt - TOKEN_EXPIRY_SKEW_MS) {
      await this.refreshTokens();
    }
    return this.appAccessToken();
  }

  // Forces a token refresh and returns the new access token (or null on
  // failure). Used by the interceptor to retry a request that got a 401.
  async refreshAccessToken(): Promise<string | null> {
    await this.refreshTokens();
    return this.appAccessToken();
  }

  private async exchangeMicrosoftToken(microsoftIdToken: string): Promise<void> {
    try {
      const body: LoginRequest = { token: microsoftIdToken };
      const tokens = await firstValueFrom(this.http.post<TokenResponse>(AUTH_LOGIN_URL, body));
      this.setTokens(tokens);
    } catch {
      this.clearTokens();
    }
  }

  private refreshTokens(): Promise<void> {
    if (this.refreshInFlight) return this.refreshInFlight;

    const refreshToken = this.appRefreshToken;
    if (!refreshToken) {
      this.handleExpiredToken();
      return Promise.resolve();
    }

    const body: RefreshRequest = { refresh_token: refreshToken };
    this.refreshInFlight = firstValueFrom(this.http.post<TokenResponse>(AUTH_REFRESH_URL, body))
      .then((tokens) => this.setTokens(tokens))
      .catch(() => {
        this.clearTokens();
        this.handleExpiredToken();
      })
      .finally(() => {
        this.refreshInFlight = null;
      });

    return this.refreshInFlight;
  }

  private setTokens(tokens: TokenResponse): void {
    this.appAccessToken.set(tokens.access_token);
    this.appRefreshToken = tokens.refresh_token;
    this.accessTokenExpiresAt = Date.now() + tokens.expires_in * 1000;
  }

  private clearTokens(): void {
    this.appAccessToken.set(null);
    this.appRefreshToken = null;
    this.accessTokenExpiresAt = 0;
  }

  hasRole(role: string): boolean {
    return this.user()?.roles.includes(role) ?? false;
  }

  isTokenExpired(): boolean {
    if (this.isLocalhost()) return false;
    const user = this.user();
    if (!user?.expiresOn) return false;
    return new Date(user.expiresOn).getTime() <= Date.now();
  }

  handleExpiredToken(): void {
    if (this.handlingExpiredToken) return;
    this.handlingExpiredToken = true;

    this.clearAzureCookies();
    this.tokenExpired.set(true);
  }

  private isLocalhost(): boolean {
    const hostname = this.document.defaultView?.location.hostname ?? '';
    return hostname === 'localhost' || hostname === '127.0.0.1';
  }

  clearAzureCookies(): void {
    const cookies = this.document.cookie.split(';');
    for (const cookie of cookies) {
      const name = cookie.split('=')[0].trim();
      if (name) {
        this.document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
      }
    }
  }

  private mapToUserInfo(authMe: AuthMeResponse): UserInfo {
    const claims = authMe.user_claims;
    return {
      userId: authMe.user_id,
      name: this.findClaimValue(claims, CLAIM_TYPE_NAME),
      email: this.findClaimValue(claims, CLAIM_TYPE_EMAIL),
      roles: this.findAllClaimValues(claims, CLAIM_TYPE_ROLES, CLAIM_TYPE_ROLES_SHORT),
      expiresOn: authMe.expires_on,
      claims,
    };
  }

  private findClaimValue(claims: AuthMeUserClaim[], type: string): string {
    return claims.find((c) => c.typ === type)?.val ?? '';
  }

  private findAllClaimValues(claims: AuthMeUserClaim[], ...types: string[]): string[] {
    return claims.filter((c) => types.includes(c.typ)).map((c) => c.val);
  }
}
