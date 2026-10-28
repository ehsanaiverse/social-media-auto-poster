import { GitBranch, History, Inbox, type LucideIcon, Play, ScanLine, ShieldCheck, Sparkles } from 'lucide-react';

export interface SectionLink {
  id: string;
  label: string;
  icon: LucideIcon;
}

// Order matches the page.
export const SECTIONS: SectionLink[] = [
  { id: 'overview', label: 'Home', icon: Sparkles },
  { id: 'intake', label: 'New post', icon: Inbox },
  { id: 'scan', label: 'Queue', icon: ScanLine },
  { id: 'runner', label: 'Publish', icon: Play },
  { id: 'workflow', label: 'How it works', icon: GitBranch },
  { id: 'jobs', label: 'History', icon: History },
  { id: 'linkedin', label: 'Accounts', icon: ShieldCheck }
];

export function scrollToSection(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  history.replaceState(null, '', `#${id}`);
}
