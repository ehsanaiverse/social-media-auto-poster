import React, { useEffect, useId, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertCircle, CheckCircle2, FolderInput, Globe, Hash, ImagePlus, Inbox, Loader2, Monitor, Send, Smartphone, X } from 'lucide-react';
import { useApi } from '../context/ApiContext';
import { MAX_CAPTION_BYTES, MAX_REAL_CAPTION_CHARS, captionLength, imageErrors, postNameError, utf8Bytes } from '../lib/postRules';
import { PLATFORMS, platformById } from '../lib/platforms';
import { cn } from '../utils/cn';

const LinkedInMark = platformById('linkedin').Icon;
import { SectionHeading } from './ui/SectionHeading';
import { EASE_OUT, Reveal, SpotlightCard } from './ui/motion';

const TAGS = ['#Hiring', '#Engineering', '#Automation', '#BuildInPublic', '#Startups'];

export const IntakeStudio: React.FC = () => {
  const { createPost, isCreatingPost, linkedInStatus, connection } = useApi();
  const fileInputId = useId();

  const [name, setName] = useState('');
  const [caption, setCaption] = useState('');
  const [image, setImage] = useState<File | null>(null);
  const [imageProblems, setImageProblems] = useState<string[]>([]);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [device, setDevice] = useState<'desktop' | 'mobile'>('desktop');
  const [isDragOver, setIsDragOver] = useState(false);
  const [touched, setTouched] = useState(false);

  // Free the preview URL when the image changes or the section unmounts.
  useEffect(() => {
    if (!image) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(image);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  const nameError = postNameError(name);
  const bytes = utf8Bytes(caption);
  const chars = captionLength(caption);
  const captionError = !caption.trim()
    ? 'Please write a caption.'
    : bytes > MAX_CAPTION_BYTES
      ? 'The caption is too long.'
      : null;
  const tooLongForReal = chars > MAX_REAL_CAPTION_CHARS;
  const canSubmit = connection === 'online' && !nameError && !captionError && image && imageProblems.length === 0 && !isCreatingPost;

  const pickImage = async (file: File) => {
    setImage(file);
    setImageProblems(await imageErrors(file));
    if (!name.trim()) setName(file.name.replace(/\.[^.]+$/, ''));
  };

  const clearImage = () => {
    setImage(null);
    setImageProblems([]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!canSubmit || !image) return;
    const res = await createPost({ name: name.trim(), image, caption });
    if (res) {
      setName('');
      setCaption('');
      clearImage();
      setTouched(false);
    }
  };

  const author = linkedInStatus?.member?.name ?? 'You';
  const checks = [
    { ok: !nameError, label: 'Post has a name' },
    { ok: Boolean(image) && imageProblems.length === 0, label: 'Picture added' },
    { ok: !captionError, label: 'Caption written' },
    { ok: !tooLongForReal, label: 'Fits LinkedIn’s length limit' }
  ];

  return (
    <section id="intake" className="relative py-16 lg:py-20">
      <div className="page-container">
        <SectionHeading index="01" icon={Inbox} eyebrow="Step 1" title="Create a post">
          Add a picture and write your caption. We check everything and add it to your queue.
        </SectionHeading>

        <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-12">
          <Reveal className="space-y-5 lg:col-span-7">
            <form onSubmit={handleSubmit} noValidate className="surface space-y-6 p-6">
              {/* Platforms: only LinkedIn can be chosen for now */}
              <fieldset>
                <legend className="mb-2 text-sm font-medium text-slate-200">Post to</legend>
                <div className="grid grid-cols-2 gap-2">
                  {PLATFORMS.map((p) => (
                    <label
                      key={p.id}
                      className={cn(
                        'relative flex min-h-12 items-center gap-2.5 rounded-xl border px-3 text-sm transition-colors',
                        p.live
                          ? 'cursor-pointer border-emerald-500/50 bg-emerald-500/[0.08] text-slate-100'
                          : 'cursor-not-allowed border-white/[0.06] bg-black/20 text-slate-500'
                      )}
                      title={p.live ? undefined : `${p.name} is coming soon`}
                    >
                      <input type="checkbox" checked={p.live} disabled={!p.live} readOnly className="sr-only" />
                      <p.Icon className="h-4 w-4 shrink-0" />
                      <span className="truncate">{p.name}</span>
                      {p.live ? (
                        <span className="ml-auto grid h-4 w-4 place-items-center rounded-full bg-emerald-400 text-[#04110B]">
                          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                        </span>
                      ) : (
                        <span className="ml-auto text-[10px] uppercase tracking-wide">Soon</span>
                      )}
                    </label>
                  ))}
                </div>
              </fieldset>

              {/* Name */}
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="post-name" className="text-sm font-medium text-slate-200">
                    Post name
                  </label>
                </div>
                <input
                  id="post-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={() => setTouched(true)}
                  placeholder="e.g. product-launch"
                  autoComplete="off"
                  spellCheck={false}
                  aria-invalid={touched && Boolean(nameError)}
                  aria-describedby="post-name-help"
                  className="field text-sm"
                />
                <p id="post-name-help" className={cn('mt-1.5 text-xs', touched && nameError ? 'text-rose-300' : 'text-slate-500')}>
                  {touched && nameError ? (
                    nameError
                  ) : (
                    'A short name so you can find this post later.'
                  )}
                </p>
              </div>

              {/* Image */}
              <div>
                <span className="mb-2 block text-sm font-medium text-slate-200">Picture</span>
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragOver(true);
                  }}
                  onDragLeave={() => setIsDragOver(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setIsDragOver(false);
                    const file = e.dataTransfer.files?.[0];
                    if (file) void pickImage(file);
                  }}
                  className={cn(
                    'relative overflow-hidden rounded-xl border-2 border-dashed transition-colors duration-200',
                    isDragOver ? 'border-emerald-400 bg-emerald-500/[0.06]' : 'border-white/10 bg-black/20 hover:border-white/20'
                  )}
                >
                  <input
                    id={fileInputId}
                    type="file"
                    accept=".jpg,.jpeg,.png,image/jpeg,image/png"
                    className="sr-only"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void pickImage(file);
                      e.target.value = '';
                    }}
                  />
                  <AnimatePresence mode="wait" initial={false}>
                    {previewUrl && image ? (
                      <motion.div
                        key="preview"
                        initial={{ opacity: 0, scale: 0.98 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0 }}
                        className="group relative flex items-center gap-4 p-3"
                      >
                        <img src={previewUrl} alt="" className="h-24 w-32 shrink-0 rounded-lg object-cover ring-1 ring-white/10" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-mono text-sm text-slate-200">{image.name}</p>
                          <p className="mt-0.5 font-mono text-xs text-slate-500">{(image.size / 1024).toFixed(0)} KB</p>
                          <label htmlFor={fileInputId} className="mt-2 inline-block text-xs font-medium text-emerald-300 hover:text-emerald-200">
                            Replace
                          </label>
                        </div>
                        <button
                          type="button"
                          onClick={clearImage}
                          aria-label="Remove picture"
                          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-white"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </motion.div>
                    ) : (
                      <motion.label
                        key="empty"
                        htmlFor={fileInputId}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="flex flex-col items-center px-6 py-10 text-center"
                      >
                        <motion.span
                          animate={isDragOver ? { y: -4, scale: 1.08 } : { y: 0, scale: 1 }}
                          className="grid h-12 w-12 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-500/25"
                        >
                          <ImagePlus className="h-6 w-6" aria-hidden="true" />
                        </motion.span>
                        <span className="mt-3 text-sm text-slate-200">
                          Drop a picture here or <span className="text-emerald-300 underline underline-offset-4">browse</span>
                        </span>
                        <span className="mt-1 font-mono text-[11px] text-slate-500">JPG or PNG, up to 10 MB</span>
                      </motion.label>
                    )}
                  </AnimatePresence>
                </div>
                {imageProblems.length > 0 && (
                  <ul className="mt-2 space-y-1 text-xs text-rose-300" role="alert">
                    {imageProblems.map((p) => (
                      <li key={p} className="flex items-start gap-1.5">
                        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        {p}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Caption */}
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="post-caption" className="text-sm font-medium text-slate-200">
                    Caption
                  </label>
                  <span className={cn('font-mono text-[11px]', tooLongForReal ? 'text-amber-300' : 'text-slate-500')}>
                    {chars.toLocaleString()} / {MAX_REAL_CAPTION_CHARS.toLocaleString()}
                  </span>
                </div>
                <textarea
                  id="post-caption"
                  rows={7}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                  placeholder="What do you want to share?"
                  aria-invalid={touched && Boolean(captionError)}
                  aria-describedby="post-caption-help"
                  className="field resize-y text-sm leading-relaxed"
                />
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/5">
                  <motion.div
                    className={cn('h-full rounded-full', tooLongForReal ? 'bg-amber-400' : 'bg-emerald-400/70')}
                    animate={{ width: `${Math.min(100, (chars / MAX_REAL_CAPTION_CHARS) * 100)}%` }}
                    transition={{ duration: 0.3, ease: EASE_OUT }}
                  />
                </div>
                <p id="post-caption-help" className={cn('mt-1.5 text-xs', (touched && captionError) || tooLongForReal ? 'text-amber-300' : 'text-slate-500')}>
                  {touched && captionError
                    ? captionError
                    : tooLongForReal
                      ? 'Too long for LinkedIn. Please keep it under 3,000 characters.'
                      : 'Hashtags like #hiring work as usual.'}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <Hash className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
                  {TAGS.map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      disabled={caption.includes(tag)}
                      onClick={() => setCaption((c) => (c.trim() ? `${c.trimEnd()} ${tag}` : tag))}
                      className="min-h-8 rounded-full border border-white/[0.06] bg-white/[0.03] px-2.5 font-mono text-xs text-emerald-300 transition-colors hover:border-emerald-500/30 hover:bg-emerald-500/10 disabled:opacity-35"
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              </div>

              <button type="submit" disabled={!canSubmit} className="btn btn-primary w-full">
                {isCreatingPost ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Send className="h-4 w-4" aria-hidden="true" />}
                {isCreatingPost ? 'Adding…' : 'Add to queue'}
              </button>
            </form>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {checks.map((c) => (
                <div key={c.label} className="flex items-center gap-2 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 text-xs">
                  <motion.span key={String(c.ok)} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
                    {c.ok ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-400" aria-label="Passed" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-slate-500" aria-label="Not yet" />
                    )}
                  </motion.span>
                  <span className={c.ok ? 'text-slate-300' : 'text-slate-500'}>{c.label}</span>
                </div>
              ))}
            </div>
          </Reveal>

          {/* Preview */}
          <Reveal delay={0.1} className="space-y-4 lg:sticky lg:top-28 lg:col-span-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-slate-200">Preview</h3>
              <div className="segmented" role="radiogroup" aria-label="Preview size">
                {(
                  [
                    ['desktop', Monitor],
                    ['mobile', Smartphone]
                  ] as const
                ).map(([d, Icon]) => (
                  <button
                    key={d}
                    type="button"
                    role="radio"
                    aria-checked={device === d}
                    aria-label={d === 'desktop' ? 'Desktop' : 'Mobile'}
                    onClick={() => setDevice(d)}
                    className="relative grid h-8 w-10 place-items-center rounded-lg"
                  >
                    {device === d && <motion.span layoutId="device-pill" className="absolute inset-0 rounded-lg bg-white/10" />}
                    <Icon className={cn('relative h-4 w-4', device === d ? 'text-slate-100' : 'text-slate-500')} />
                  </button>
                ))}
              </div>
            </div>

            <motion.div layout transition={{ duration: 0.4, ease: EASE_OUT }} className={cn(device === 'mobile' ? 'mx-auto max-w-[340px]' : 'w-full')}>
              <SpotlightCard className="overflow-hidden">
                <div className="flex items-center gap-3 p-4">
                  <span className="grid h-10 w-10 place-items-center rounded-full bg-gradient-to-tr from-emerald-600 to-teal-400 text-sm font-semibold text-white">
                    {author.charAt(0).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-100">{author}</p>
                    <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
                      Now · <Globe className="h-3 w-3" aria-hidden="true" /> Anyone
                    </p>
                  </div>
                  <span className="flex items-center gap-1.5 rounded-full bg-[#0A66C2]/15 px-2.5 py-1 text-[11px] font-medium text-sky-200 ring-1 ring-inset ring-[#0A66C2]/40">
                    <LinkedInMark className="h-3 w-3" /> LinkedIn
                  </span>
                </div>
                <div className="whitespace-pre-wrap break-words px-4 pb-4 text-sm leading-relaxed text-slate-200">
                  {caption ? (
                    caption.split(/(#[\p{L}\p{N}_]+)/u).map((part, i) =>
                      part.startsWith('#') ? (
                        <span key={i} className="font-medium text-sky-300">
                          {part}
                        </span>
                      ) : (
                        <React.Fragment key={i}>{part}</React.Fragment>
                      )
                    )
                  ) : (
                    <span className="italic text-slate-500">Your caption appears here.</span>
                  )}
                </div>
                {/* The whole picture at its own shape, never cropped. */}
                <div className="w-full border-t border-white/[0.06] bg-black/40">
                  {previewUrl ? (
                    <motion.img
                      key={previewUrl}
                      src={previewUrl}
                      alt="Post image preview"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ duration: 0.4 }}
                      className="mx-auto block h-auto max-h-[640px] w-full object-contain"
                    />
                  ) : (
                    <div className="grid aspect-[1.91/1] place-items-center text-xs text-slate-600">No picture yet</div>
                  )}
                </div>
              </SpotlightCard>
            </motion.div>
            <p className="text-xs leading-relaxed text-slate-500">
              A preview of your post. Nothing is posted until you publish.
            </p>
            <div className="flex items-start gap-3 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-400/25">
                <FolderInput className="h-4 w-4" aria-hidden="true" />
              </span>
              <p className="text-sm leading-relaxed text-slate-400">
                <span className="font-medium text-slate-200">Prefer the folder?</span> Drop a picture and a caption file with the same
                name (like <span className="text-slate-300">launch.jpg</span> + <span className="text-slate-300">launch.txt</span>) into your posts folder. It shows up in your queue on its own.
              </p>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
};
