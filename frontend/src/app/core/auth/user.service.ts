import { computed, inject, Injectable, signal } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { AuthMeResponse, AuthMeUserClaim, UserInfo } from './auth.models';

const CLAIM_TYPE_NAME = 'name';
const CLAIM_TYPE_EMAIL = 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress';
const CLAIM_TYPE_ROLES = 'http://schemas.microsoft.com/ws/2008/06/identity/claims/role';
const CLAIM_TYPE_ROLES_SHORT = 'roles';
const example = [
    {
        "access_token": "eyJ0eXAiOiJKV1QiLCJub25jZSI6Imw5S3BlSlRRdkluN2Jzb0V4Y3J0czRUYTVQRVBMaEI0SjNWYlhpZFJvV28iLCJhbGciOiJSUzI1NiIsIng1dCI6IlFaZ045SHFOa0dORU00R2VLY3pEMDJQY1Z2NCIsImtpZCI6IlFaZ045SHFOa0dORU00R2VLY3pEMDJQY1Z2NCJ9.eyJhdWQiOiIwMDAwMDAwMy0wMDAwLTAwMDAtYzAwMC0wMDAwMDAwMDAwMDAiLCJpc3MiOiJodHRwczovL3N0cy53aW5kb3dzLm5ldC82ZWFiNjM2NS04MTk0LTQ5YzYtYTRkMC1lMmQxYTBmYmViNzQvIiwiaWF0IjoxNzczODE4OTg2LCJuYmYiOjE3NzM4MTg5ODYsImV4cCI6MTc3MzgyNDYwMCwiYWNjdCI6MCwiYWNyIjoiMSIsImFpbyI6IkFYUUFpLzhiQUFBQThJL0hoaWJwZnZqMkpnaTErSzJNMy9aR1p1Ly9mVGUzMEhicG0rZm5OSUNwN285eHNFYkl0U3lMUDNSaTVoa3dEbUh1TUx4YnpYeUNSK085OW95elZSU0JFY21zTGNlZmIwS2oyQ3NJZnFHN2w3dml1cndhcE4zbmRrdTFvOFZzdTB3Y3ZJaTc3bW1uc21DTkdKNEpXUT09IiwiYW1yIjpbInJzYSJdLCJhcHBfZGlzcGxheW5hbWUiOiJhcy1hcHBsaS1zd2UtdHQtc284LWZyb250IiwiYXBwaWQiOiI2NTY5NWFjNy05OGRiLTQyZjUtYjE1Zi05YWM5ZDBmZmQ0YzMiLCJhcHBpZGFjciI6IjEiLCJkZXZpY2VpZCI6IjU2Nzc1NDNkLWU0NTYtNDc5Mi05OTlmLTllMTY1OTY0YmVjZCIsImZhbWlseV9uYW1lIjoiTUFMVEFCIiwiZ2l2ZW5fbmFtZSI6IkNkcyIsImlkdHlwIjoidXNlciIsImlwYWRkciI6IjE1OC4xNTYuMTYzLjE0IiwibmFtZSI6Ik1hbHRhYiwgQ2RzIChFeHQpIiwib2lkIjoiMzljYTU4NjUtMWNiNi00ZmU4LTlmNTItZjFiZDE5ZTZjMWU0Iiwib25wcmVtX3NpZCI6IlMtMS01LTIxLTU3OTg5ODQxLTE3NzAwMjczNzItNjgyMDAzMzMwLTQ1NTQ4OCIsInBsYXRmIjoiMyIsInB1aWQiOiIxMDAzMjAwNTEwQ0I2ODA0IiwicmgiOiIxLkFTQUFaV09yYnBTQnhrbWswT0xSb1B2cmRBTUFBQUFBQUFBQXdBQUFBQUFBQUFBZ0FMZ2dBQS4iLCJzY3AiOiJVc2VyLlJlYWQgcHJvZmlsZSBvcGVuaWQgZW1haWwiLCJzaWQiOiIwMGE5ZGM3OS1iMjM3LTg2MWItZTg4Yi0zNWRjNmNjNjRhYTMiLCJzaWduaW5fc3RhdGUiOlsiZHZjX21uZ2QiLCJkdmNfZG1qZCIsImlua25vd25udHdrIiwia21zaSJdLCJzdWIiOiJGX1oxWU1QWXBhdXRNaHE5c1QycmhmbVFHVTlSR1VzalY0dUxaTjYxLWRRIiwidGVuYW50X3JlZ2lvbl9zY29wZSI6IkVVIiwidGlkIjoiNmVhYjYzNjUtODE5NC00OWM2LWE0ZDAtZTJkMWEwZmJlYjc0IiwidW5pcXVlX25hbWUiOiJjZHMubWFsdGFiLWVAY2Fpc3NlZGVzZGVwb3RzLmZyIiwidXBuIjoiY2RzLm1hbHRhYi1lQGNhaXNzZWRlc2RlcG90cy5mciIsInV0aSI6Ik5nWjlpMmNkT1V1OVlDUG5jQjBtQUEiLCJ2ZXIiOiIxLjAiLCJ3aWRzIjpbImI3OWZiZjRkLTNlZjktNDY4OS04MTQzLTc2YjE5NGU4NTUwOSJdLCJ4bXNfYWNkIjoxNzczMjIxMzMzLCJ4bXNfYWN0X2ZjdCI6IjMgOSIsInhtc19mdGQiOiI3di1IcnFpUXlWcE1sWVNXeThwRXAtbkJxUTVER19GUElmWkktNjM4TVBJQlpYVnliM0JsYm05eWRHZ3RaSE50Y3ciLCJ4bXNfaWRyZWwiOiIxIDI0IiwieG1zX3N0Ijp7InN1YiI6Ii1xTmJ5aGtqVVVFNU84TFQzY19HbzRzbUNLcVpVT016Y3F1QXhodEtWUU0ifSwieG1zX3N1Yl9mY3QiOiI0IDMiLCJ4bXNfdGNkdCI6MTQ1NTgwMTI0NSwieG1zX3RkYnIiOiJFVSIsInhtc190bnRfZmN0IjoiMyA4In0.gmrlk37zvfp42mLPjYNs6S6fCxlXsx0vBJ5ORr5yrPNvzioDB0ZUigvZR3IB_Qv9uvvwVA3bzFxWj3uCsodWDQjB4IX6iAv0LmPMgy_aU929yJ6PqLkv1sDUcsKRMH4wpr5Im3wmjZzyAQo2HrjbIDVGK90mL7v-7VJ2BKNzqcG_fqGGIhqZd4Xp2Q1dWM0YBYsveBxlZjKnWfvrmKflbm13Lp4XOLlVyMf0sgvxhTIbiralXTjdr2Z_27USgyMxems9RBoHvQEQQ_XlNvqTRBADDLJlTNw5JzC-KPeR78kscVc3GM5jMST-mvtU7Vyn_5zhvUnZxvsRBhhSg54ulg",
        "expires_on": "2026-03-18T09:03:19.4665586Z",
        "id_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImtpZCI6IlFaZ045SHFOa0dORU00R2VLY3pEMDJQY1Z2NCJ9.eyJhdWQiOiI2NTY5NWFjNy05OGRiLTQyZjUtYjE1Zi05YWM5ZDBmZmQ0YzMiLCJpc3MiOiJodHRwczovL2xvZ2luLm1pY3Jvc29mdG9ubGluZS5jb20vNmVhYjYzNjUtODE5NC00OWM2LWE0ZDAtZTJkMWEwZmJlYjc0L3YyLjAiLCJpYXQiOjE3NzM4MTg5ODYsIm5iZiI6MTc3MzgxODk4NiwiZXhwIjoxNzczODIyODg2LCJhaW8iOiJBWlFBYS84YkFBQUFwT1hCdFdXdnJ4a0UzYXU0NTJkVUhpOWhOaGZQelZSV0ZHQkNRUjZKYWo5VXZNTXo0c0dWQitYTlFldCtYWlp0R2Rwd1JGUFNNZlFsaGcrTWdBRnJVdlBQc1FaUGJRT1JRZ2Y0dWYrbjBDL3lVUnMzTzFYakVzbFJKaFpLeGFlYW0zL1NGY3k1VUZCNFVla2pabXJRSHpUNHBTYjFVb0xFeVU2WG80dm5XYlAvb0RzVVFkWnVJZmFqUWpWSjhhMm8iLCJlbWFpbCI6ImNkcy5tYWx0YWItZUBjYWlzc2VkZXNkZXBvdHMuZnIiLCJuYW1lIjoiTWFsdGFiLCBDZHMgKEV4dCkiLCJub25jZSI6ImUzNzM4N2U5ODYzZTRmNjk4OTM2YmNlZmZjZWQ1NTk4XzIwMjYwMzE4MDczOTM5Iiwib2lkIjoiMzljYTU4NjUtMWNiNi00ZmU4LTlmNTItZjFiZDE5ZTZjMWU0IiwicHJlZmVycmVkX3VzZXJuYW1lIjoiY2RzLm1hbHRhYi1lQGNhaXNzZWRlc2RlcG90cy5mciIsInJoIjoiMS5BU0FBWldPcmJwU0J4a21rME9MUm9QdnJkTWRhYVdYYm1QVkNzVi1heWREXzFNTWdBTGdnQUEuIiwic2lkIjoiMDBhOWRjNzktYjIzNy04NjFiLWU4OGItMzVkYzZjYzY0YWEzIiwic3ViIjoiLXFOYnloa2pVVUU1TzhMVDNjX0dvNHNtQ0txWlVPTXpjcXVBeGh0S1ZRTSIsInRpZCI6IjZlYWI2MzY1LTgxOTQtNDljNi1hNGQwLWUyZDFhMGZiZWI3NCIsInV0aSI6Ik5nWjlpMmNkT1V1OVlDUG5jQjBtQUEiLCJ2ZXIiOiIyLjAifQ.AH6OhSHf1R8Outjl9YAVHFSsS8ObPQikyphm6h3U6vapbsUn0-wcZr9cm30Zntvfum-ZNSywJFHfLw0uEDBw4PmsvfoPcoe13-Cr_X8JTZsT501Xwkfd6abaYpuw1Of2RI6f2dLynh5uRO7t_r1pgNv7c1dgoYVINTkJSW1todSCL5g8PD2uYvbkwLbcwH6zhziYjIM2dxTjlJ_rmgBFbvnIkotG794mRi2-2tPqArvzjtoGrDsAx1SZ2ErneUFg5Z5vWB7TA7SnWeOp1kTbjNx5U-mJcfPF_Ru2VE_eoZXBmeRsXuVBa2XkEu-pVmXnorlx_op_Gtd450hwQ29RzA",
        "provider_name": "aad",
        "user_claims": [
            {
                "typ": "aud",
                "val": "65695ac7-98db-42f5-b15f-9ac9d0ffd4c3"
            },
            {
                "typ": "iss",
                "val": "https://login.microsoftonline.com/6eab6365-8194-49c6-a4d0-e2d1a0fbeb74/v2.0"
            },
            {
                "typ": "iat",
                "val": "1773818985"
            },
            {
                "typ": "nbf",
                "val": "1773818985"
            },
            {
                "typ": "exp",
                "val": "1773822885"
            },
            {
                "typ": "aio",
                "val": "AWQAm/8bAAAA5tQjLcKU4jYILMMWHBfdi4LTAcDBcg6i7Cu1sxLeQ6z9Ta4rrbLCNBG8Ri4O+FtR59TFmJQm1oUCI4UmcsL61Zc7qCGYw/xtWRkWzGcgHSRVr1Md6HG9SeXX8M9ATfux"
            },
            {
                "typ": "c_hash",
                "val": "koU6R0OCZDakVsaVrwkdMA"
            },
            {
                "typ": "cc",
                "val": "CgEAEhJjYWlzc2VkZXNkZXBvdHMuZnIaEgoQb/Um/Ls1fkKpPPcpIqeHOSISChBnhIMNUaxjS7dsJzLBjiQAKAEyAkVVOAA="
            },
            {
                "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                "val": "cds.maltab-e@caissedesdepots.fr"
            },
            {
                "typ": "name",
                "val": "Maltab, Cds (Ext)"
            },
            {
                "typ": "nonce",
                "val": "e37387e9863e4f698936bceffced5598_20260318073939"
            },
            {
                "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "val": "39ca5865-1cb6-4fe8-9f52-f1bd19e6c1e4"
            },
            {
                "typ": "preferred_username",
                "val": "cds.maltab-e@caissedesdepots.fr"
            },
            {
                "typ": "rh",
                "val": "1.ASAAZWOrbpSBxkmk0OLRoPvrdMdaaWXbmPVCsV-aydD_1MMgALggAA."
            },
            {
                "typ": "sid",
                "val": "00a9dc79-b237-861b-e88b-35dc6cc64aa3"
            },
            {
                "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
                "val": "-qNbyhkjUUE5O8LT3c_Go4smCKqZUOMzcquAxhtKVQM"
            },
            {
                "typ": "http://schemas.microsoft.com/identity/claims/tenantid",
                "val": "6eab6365-8194-49c6-a4d0-e2d1a0fbeb74"
            },
            {
                "typ": "uti",
                "val": "Z4SDDVGsY0u3bCcywY4kAA"
            },
            {
                "typ": "ver",
                "val": "2.0"
            }
        ],
        "user_id": "cds.maltab-e@caissedesdepots.fr"
    }
]

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
      const response = example;
        this.user.set(this.mapToUserInfo(response[0]));
      // this.user.set(null);
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
    console.log('Clearing cookies:', cookies);
    for (const cookie of cookies) {
      const name = cookie.split('=')[0].trim();
      if (name) {
        console.log(`Clearing cookie: ${name}`);
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
