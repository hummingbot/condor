import { useQuery } from "@tanstack/react-query";

import { ADMIN_PEOPLE_KEY, adminApi } from "@/lib/admin-api";

/**
 * Whether this seat is an admin, learned by asking an admin-only route.
 *
 * There is no `is_admin` claim on the client, so the admin surface *answering*
 * is the discriminator: `/admin/people` is a 403 for everyone else. The query
 * key is the one the panel itself uses, so the probe and the panel dedupe into
 * a single request.
 *
 * This is only ever a reason to hide a control. `require_admin` re-reads the
 * role from the ConfigManager on every request and is the gate; a seat that
 * lies to this hook gains nothing.
 */
export function useIsAdmin(): boolean {
  const { isSuccess } = useQuery({
    queryKey: ADMIN_PEOPLE_KEY,
    queryFn: adminApi.getPeople,
    retry: false,
  });
  return isSuccess;
}
