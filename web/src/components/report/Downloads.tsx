// Download links for a backend report: Markdown, PDF and the whole run folder (zip).
// Hidden for the hand-written fixture reports, which have no files on the backend.

import { Download, FileArchive, FileText, FileType } from 'lucide-react'
import { downloadUrl, hasDownloads, type DownloadFormat } from '../../api'

const FORMATS: { format: DownloadFormat; label: string; title: string; icon: typeof FileText }[] = [
  { format: 'md', label: 'Markdown', title: 'Report with numbered sources for every claim (.md)', icon: FileText },
  {
    format: 'pdf',
    label: 'PDF',
    title: 'Formatted report (.pdf). The first download of an older run may take a minute while it is generated.',
    icon: FileType,
  },
  { format: 'zip', label: 'All files', title: 'Whole run folder: JSON, Markdown, PDF, traces, analyst prompts (.zip)', icon: FileArchive },
]

export default function Downloads({ id, className = '' }: { id: string; className?: string }) {
  if (!hasDownloads(id)) return null
  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      <span className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-slate-500 uppercase">
        <Download className="h-3.5 w-3.5" />
        Download
      </span>
      {FORMATS.map(({ format, label, title, icon: Icon }) => (
        <a
          key={format}
          href={downloadUrl(id, format)}
          download
          title={title}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-sm text-slate-700 shadow-xs hover:bg-slate-50 hover:text-slate-900"
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
        </a>
      ))}
    </div>
  )
}
