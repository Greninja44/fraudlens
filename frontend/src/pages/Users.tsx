import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import { dt, humanize } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type { User } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardBody, CardHeader, Field, Input, Select, Table, Td, Th } from "@/components/ui/primitives";
import { ErrorState, QueryState } from "@/components/ui/states";
import { PageHeader } from "@/components/domain";
import { Dialog } from "@/components/ui/dialog";

const ROLE_DESC: Record<string, string> = {
  admin: "Full access: users, settings, all cases and evidence, audit log.",
  investigator: "Imports data, runs detection, manages cases they create or are assigned to, uploads evidence, generates reports.",
  reviewer: "Reads all cases, reviews alerts, verifies evidence and entity links, closes or returns cases under review, reads the audit log. Cannot import or upload.",
};

export default function UsersPage() {
  const { can, user: me } = useAuth();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users") });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const admin = can("users:manage");
  return (
    <>
      <PageHeader title="Users & Access" subtitle={admin ? "Manage accounts and role-based access." : "Only administrators can change users."}
        actions={admin && <Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" />Add user</Button>} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <QueryState q={q}>
            {(users) => (
              <Table><thead><tr><Th>Name</Th><Th>Email</Th><Th>Role</Th><Th>Status</Th><Th>Last sign-in</Th><Th /></tr></thead>
                <tbody>{users.map((u) => (
                  <tr key={u.id} className="border-b border-border/60">
                    <Td className="font-medium">{u.full_name}{u.id === me?.id && <span className="ml-1 text-xs text-subtle">(you)</span>}</Td><Td className="text-xs">{u.email}</Td>
                    <Td><Badge tone={u.role === "admin" ? "danger" : u.role === "reviewer" ? "accent" : "neutral"}>{humanize(u.role)}</Badge></Td>
                    <Td>{u.is_active ? <Badge tone="success">active</Badge> : <Badge>disabled</Badge>}</Td><Td className="text-xs text-muted">{dt(u.last_login_at)}</Td>
                    <Td>{admin && <Button size="sm" variant="ghost" onClick={() => setEditing(u)}>Edit</Button>}</Td>
                  </tr>))}</tbody></Table>
            )}
          </QueryState>
        </Card>
        <Card>
          <CardHeader title="Roles" />
          <CardBody className="space-y-3 text-xs">{Object.entries(ROLE_DESC).map(([r, d]) => <div key={r}><div className="font-semibold">{humanize(r)}</div><div className="text-muted">{d}</div></div>)}
            <p className="text-subtle">Investigators can only open cases and evidence they created or are assigned to.</p></CardBody>
        </Card>
      </div>
      {creating && <UserDialog onClose={() => setCreating(false)} />}
      {editing && <UserDialog user={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function UserDialog({ user, onClose }: { user?: User; onClose: () => void }) {
  const [f, setF] = useState({ email: user?.email ?? "", full_name: user?.full_name ?? "", role: user?.role ?? "investigator", password: "", is_active: user?.is_active ?? true });
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => user
      ? api(`/users/${user.id}`, { method: "PATCH", json: { full_name: f.full_name, role: f.role, is_active: f.is_active, password: f.password || undefined } })
      : api("/users", { method: "POST", json: { email: f.email, full_name: f.full_name, role: f.role, password: f.password } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); onClose(); },
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={user ? `Edit ${user.full_name}` : "Add user"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
      <div className="space-y-3">
        {!user && <Field label="Email"><Input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>}
        <Field label="Full name"><Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Role"><Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as User["role"] })}>{["admin", "investigator", "reviewer"].map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</Select></Field>
        <Field label={user ? "Reset password" : "Password"} hint="min 10 chars, upper + lower case and a digit"><Input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        {user && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />Account active</label>}
        {m.error && <ErrorState error={m.error} className="py-2" />}
      </div>
    </Dialog>
  );
}
