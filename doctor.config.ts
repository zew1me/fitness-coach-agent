import type { ReactDoctorConfig } from "react-doctor/api";

// React Doctor (issue #444) runs in CI and pre-push via `bun run react-doctor`. The gate
// fails on warnings as well as errors, matching ESLint's zero-warnings policy.
const config: ReactDoctorConfig = {
  blocking: "warning",
  // Telemetry, score upload and share URLs send repository metadata to a third party.
  // The gate needs only the local diagnostics.
  noScore: true,
  share: false,
  // The supply-chain check scores every dependency against Socket.dev over the network.
  // That makes the gate's result depend on an external service, so the same commit could
  // pass on one run and fail on the next. Dependency changes are reviewed by hand in the
  // PR instead.
  supplyChain: { enabled: false },
  ignore: {
    // This rule reads each migration on its own. It flags tables created in
    // 0001_schema.sql and two early migrations because RLS is not enabled in the same
    // file. 20260816191358_rls_and_security.sql enables RLS on all 18 flagged tables, and
    // tests/python/test_rls_matrix_db.py asserts the policies per role, so the finding
    // does not hold for the schema as deployed.
    rules: ["react-doctor/supabase-table-missing-rls"],
  },
};

export default config;
