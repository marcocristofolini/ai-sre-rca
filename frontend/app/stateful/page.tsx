import { redirect } from 'next/navigation';

export default function StatefulIndexPage() {
  redirect('/stateful/postgresql');
}
