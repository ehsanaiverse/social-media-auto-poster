import React from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'outline' | 'ghost'
  size?: 'sm' | 'default' | 'lg'
}

export function Button({ className, variant = 'default', size = 'default', ...props }: ButtonProps) {
  const variants = {
    default: 'bg-sky-500 hover:bg-sky-400 text-slate-950 font-semibold shadow-sm hover:shadow-md hover:shadow-sky-500/20 active:scale-[0.98]',
    outline: 'border border-slate-700/80 hover:border-slate-600 bg-slate-900/40 hover:bg-slate-800/60 text-slate-200 active:scale-[0.98]',
    ghost: 'hover:bg-slate-800/60 text-slate-300 hover:text-white',
  }
  const sizes = {
    sm: 'px-3 py-1.5 text-sm',
    default: 'px-4 py-2',
    lg: 'px-6 py-3 text-lg',
  }
  return (
    <button
      className={`rounded-lg font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
      {...props}
    />
  )
}