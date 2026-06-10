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
  accessToken: string;
  expiresOn: string;
  claims: AuthMeUserClaim[];
}
