import { redirect } from "next/navigation";

export default async function ReplayRedirect({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(/^[0-9a-f]{16}$/.test(id) ? `/replay?id=${id}` : "/replay");
}
