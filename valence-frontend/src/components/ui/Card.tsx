import React from 'react';

interface CardProps {
  children: React.ReactNode;
  padding?: 'sm' | 'md' | 'lg' | 'none';
  glow?: boolean;
  hover?: boolean;
  className?: string;
  onClick?: () => void;
}

export const Card: React.FC<CardProps> = ({
  children,
  padding = 'md',
  glow = false,
  hover = false,
  className = '',
  onClick,
}) => {
  const paddingClass = {
    none: '',
    sm: 'p-3',
    md: 'p-4',
    lg: 'p-5',
  }[padding];

  return (
    <div
      onClick={onClick}
      className={`
        bg-white rounded-xl border border-slate-200 shadow-xs
        ${paddingClass}
        ${glow ? 'border-blue-300 shadow-sm' : ''}
        ${hover ? 'hover:border-blue-400 hover:shadow-md transition-all cursor-pointer' : ''}
        ${className}
      `}
    >
      {children}
    </div>
  );
};
