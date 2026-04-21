"""Cloudflare API client for R2 bucket provisioning and token management."""

import httpx

_CF_BASE = "https://api.cloudflare.com/client/v4"

# Permission group name fragments to match for R2 access.
# Cloudflare uses "Workers R2 Storage: Edit" (read+write) and
# "Workers R2 Storage: Read" (read-only). We request both for the scoped token.
_R2_PERMISSION_NAMES = {"Workers R2 Storage: Edit", "Workers R2 Storage: Read"}


class CloudflareClient:
    def __init__(self, api_token: str, account_id: str, dry_run: bool = False) -> None:
        self._account_id = account_id
        self._dry_run = dry_run
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def _get(self, path: str, **kwargs) -> dict:
        r = self._http.get(f"{_CF_BASE}{path}", **kwargs)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(f"{_CF_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: dict) -> dict:
        r = self._http.put(f"{_CF_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = self._http.delete(f"{_CF_BASE}{path}")
        r.raise_for_status()
        return r.json()

    def ensure_bucket(self, env: str) -> str:
        """Create the R2 bucket for this env if it doesn't exist. Returns bucket name."""
        name = f"fitness-coach-agent-{env}"
        acct = self._account_id

        data = self._get(f"/accounts/{acct}/r2/buckets")
        existing = {b["name"] for b in data.get("result", {}).get("buckets", [])}

        if name in existing:
            print(f"  R2 bucket {name!r} already exists.")
        else:
            print(f"  Creating R2 bucket {name!r}…")
            if not self._dry_run:
                self._post(f"/accounts/{acct}/r2/buckets", {"name": name})
                print(f"  Created R2 bucket {name!r}.")
            else:
                print(f"  [dry-run] Would create R2 bucket {name!r}.")

        return name

    def ensure_cors(self, bucket_name: str, allowed_origins: list[str]) -> None:
        """Set CORS rules on the bucket for the given allowed origins.

        Note: Vercel preview URLs follow the pattern
        https://{project}-{hash}-{org}.vercel.app, which cannot be predicted
        ahead of time. Callers should pass the most specific origins available
        (production domain + known preview aliases). The '*.vercel.app' wildcard
        is intentionally avoided here; pass it explicitly only if needed.
        """
        rules = [
            {
                "AllowedOrigins": allowed_origins,
                "AllowedMethods": ["PUT", "GET", "HEAD"],
                "AllowedHeaders": ["*"],
                "MaxAgeSeconds": 3600,
            }
        ]

        print(f"  Configuring CORS on {bucket_name!r} for: {allowed_origins}")
        if self._dry_run:
            print("  [dry-run] Would set CORS rules.")
            return

        self._put(
            f"/accounts/{self._account_id}/r2/buckets/{bucket_name}/cors",
            {"rules": rules},
        )
        print("  CORS configured.")

    def _get_r2_permission_group_ids(self) -> list[dict]:
        """Fetch R2 permission group IDs from the Cloudflare IAM API.

        Permission group IDs are not hardcoded — they are fetched at runtime
        to avoid depending on values that could change between API versions.
        """
        data = self._get(f"/accounts/{self._account_id}/iam/permission_groups")
        groups = data.get("result", [])
        matched = [g for g in groups if g.get("name") in _R2_PERMISSION_NAMES]
        if not matched:
            available = [g.get("name") for g in groups if "r2" in g.get("name", "").lower()]
            raise RuntimeError(
                f"Could not find R2 permission groups. Available R2 groups: {available}. "
                "Check that your CF_API_TOKEN has 'Account: Read' permission."
            )
        return [{"id": g["id"]} for g in matched]

    def ensure_r2_token(self, bucket_name: str, env: str, existing_secret: str = "") -> dict:
        """Create a scoped R2 API token for this bucket/env.

        Returns {access_key_id, secret_access_key}. The secret is only available
        at creation time — callers must persist it to the state file immediately.
        If existing_secret is provided (from state file), the existing token is
        verified and the cached secret is returned without creating a new one.
        """
        acct = self._account_id
        token_name = f"fitness-coach-agent-{env}"

        # Check if a token with this name already exists
        data = self._get(f"/accounts/{acct}/r2/tokens")
        tokens = data.get("result", [])
        existing_token = next((t for t in tokens if t.get("name") == token_name), None)

        if existing_token and existing_secret:
            access_key_id = existing_token.get("accessKeyId", "")
            print(f"  R2 token {token_name!r} already exists (reusing cached secret).")
            return {"access_key_id": access_key_id, "secret_access_key": existing_secret}

        if existing_token and not existing_secret:
            print(f"  R2 token {token_name!r} exists but no cached secret found.")
            print("  Deleting and recreating so secret can be captured…")
            if not self._dry_run:
                self._delete(f"/accounts/{acct}/r2/tokens/{existing_token['id']}")

        if self._dry_run:
            print(f"  [dry-run] Would create R2 token {token_name!r}.")
            return {"access_key_id": "dry-run-key", "secret_access_key": "dry-run-secret"}

        print("  Fetching R2 permission group IDs…")
        permission_groups = self._get_r2_permission_group_ids()

        print(f"  Creating R2 API token {token_name!r} scoped to {bucket_name!r}…")
        # Bucket resource identifier format required by Cloudflare R2 token API
        bucket_resource = f"com.cloudflare.edge.r2.bucket.{acct}_default_{bucket_name}"
        result = self._post(
            f"/accounts/{acct}/r2/tokens",
            {
                "name": token_name,
                "policies": [
                    {
                        "effect": "allow",
                        "resources": {bucket_resource: "*"},
                        "permission_groups": permission_groups,
                    }
                ],
            },
        )
        token_data = result.get("result", {})
        access_key_id = token_data.get("accessKeyId", "")
        secret_access_key = token_data.get("secretAccessKey", "")
        print(f"  Created R2 token {token_name!r}.")
        return {"access_key_id": access_key_id, "secret_access_key": secret_access_key}

    def get_public_base_url(self, bucket_name: str) -> str:
        """Return the r2.dev public URL for the bucket, or empty string if not enabled."""
        try:
            data = self._get(f"/accounts/{self._account_id}/r2/buckets/{bucket_name}")
            domains = data.get("result", {}).get("domains", [])
            for d in domains:
                if "r2.dev" in d.get("domain", ""):
                    return f"https://{d['domain']}"
        except Exception:
            pass
        return ""

    def endpoint_url(self) -> str:
        return f"https://{self._account_id}.r2.cloudflarestorage.com"

    def close(self) -> None:
        self._http.close()
