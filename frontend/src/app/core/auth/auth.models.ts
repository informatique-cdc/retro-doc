export interface AuthMeUserClaim {
  typ: string;
  val: string;
}

export interface AuthMeResponse {
  access_token: string;
  expires_on: string;
  id_token: string;
  provider_name: string;
  user_claims: AuthMeUserClaim[];
  user_id: string;
}

export interface UserInfo {
  userId: string;
  name: string;
  email: string;
  roles: string[];
  expiresOn: string;
  claims: AuthMeUserClaim[];
}

export interface LoginRequest {
  token: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}
