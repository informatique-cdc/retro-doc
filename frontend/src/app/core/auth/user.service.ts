import { computed, inject, Injectable, signal } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { AuthMeResponse, AuthMeUserClaim, UserInfo } from './auth.models';

const CLAIM_TYPE_NAME = 'name';
const CLAIM_TYPE_EMAIL = 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress';
const CLAIM_TYPE_ROLES = 'http://schemas.microsoft.com/ws/2008/06/identity/claims/role';
const CLAIM_TYPE_ROLES_SHORT = 'roles';
@Injectable({ providedIn: 'root' })
export class UserService {
  private readonly http = inject(HttpClient);
  private readonly document = inject(DOCUMENT);

  private handlingExpiredToken = false;

  readonly user = signal<UserInfo | null>(null);
  readonly isAuthenticated = computed(() => this.user() !== null);
  readonly isLoading = signal(false);
  readonly tokenExpired = signal(false);

  async loadUser(): Promise<void> {
    this.isLoading.set(true);
    try {
      const response = await firstValueFrom(this.http.get<AuthMeResponse[]>('/.auth/me'));
      if (response?.length) {
        this.user.set(this.mapToUserInfo(response[0]));
      }
    } catch {
      this.user.set(null);
    } finally {
      this.isLoading.set(false);
    }
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
      accessToken: authMe.id_token,
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
