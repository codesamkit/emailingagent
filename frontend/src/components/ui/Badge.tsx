import React from 'react';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'blue' | 'slate' | 'emerald' | 'amber' | 'red' | 'purple';
  size?: 'xs' | 'sm' | 'md';
  icon?: React.ReactNode;
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'blue',
  size = 'sm',
  icon,
  className = '',
}) => {
  const sizeClasses = {
    xs: 'px-1.5 py-0.5 text-[10px] gap-1',
    sm: 'px-2 py-0.5 text-xs gap-1.5',
    md: 'px-2.5 py-1 text-xs gap-1.5 font-medium',
  }[size];

  const variantClasses = {
    blue: 'bg-blue-950/70 text-blue-300 border-blue-600/40',
    slate: 'bg-slate-900/80 text-slate-300 border-slate-700/60',
    emerald: 'bg-emerald-950/70 text-emerald-300 border-emerald-600/40',
    amber: 'bg-amber-950/70 text-amber-300 border-amber-600/40',
    red: 'bg-red-950/70 text-red-300 border-red-600/40',
    purple: 'bg-purple-950/70 text-purple-300 border-purple-600/40',
  }[variant];

  return (
    <span
      className={`inline-flex items-center rounded-full border font-medium ${sizeClasses} ${variantClasses} ${className}`}
    >
      {icon && <span className="shrink-0">{icon}</span>}
      <span>{children}</span>
    </span>
  );
};
