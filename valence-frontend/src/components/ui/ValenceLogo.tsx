import React from 'react';

interface ValenceLogoProps {
  size?: 'sm' | 'md' | 'lg' | 'xl';
  showWordmark?: boolean;
  animate?: boolean;
  className?: string;
  theme?: 'dark' | 'light';
}

export const ValenceLogo: React.FC<ValenceLogoProps> = ({
  size = 'md',
  showWordmark = true,
  animate = false,
  className = '',
  theme = 'light',
}) => {
  const iconDimensions = {
    sm: 28,
    md: 36,
    lg: 48,
    xl: 64,
  }[size];

  const wordmarkClasses = {
    sm: 'text-base font-bold tracking-tight',
    md: 'text-xl font-bold tracking-tight',
    lg: 'text-2xl font-bold tracking-tight',
    xl: 'text-4xl font-bold tracking-tight',
  }[size];

  return (
    <div className={`flex items-center gap-2.5 select-none ${className}`}>
      {/* Atomic Orbital Symbol */}
      <div className={`relative flex items-center justify-center shrink-0 ${animate ? 'hover:scale-105 transition-transform' : ''}`}>
        <svg
          width={iconDimensions}
          height={iconDimensions}
          viewBox="0 0 100 100"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            <linearGradient id="valOrbitGradLight" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#3B82F6" />
              <stop offset="50%" stopColor="#6366F1" />
              <stop offset="100%" stopColor="#8B5CF6" />
            </linearGradient>
            <linearGradient id="valOrbitGradLight2" x1="0%" y1="100%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#2563EB" />
              <stop offset="55%" stopColor="#7C3AED" />
              <stop offset="100%" stopColor="#8B5CF6" />
            </linearGradient>
          </defs>

          {/* Orbital Ring 1 (Tilted Left -35 deg) */}
          <ellipse
            cx="50"
            cy="50"
            rx="36"
            ry="18"
            transform="rotate(-35 50 50)"
            stroke="url(#valOrbitGradLight)"
            strokeWidth="5.5"
            strokeLinecap="round"
          />

          {/* Orbital Ring 2 (Tilted Right +45 deg) */}
          <ellipse
            cx="50"
            cy="50"
            rx="36"
            ry="18"
            transform="rotate(45 50 50)"
            stroke="url(#valOrbitGradLight2)"
            strokeWidth="5.5"
            strokeLinecap="round"
          />

          {/* Orbital Electron 1 (Bottom Left in Blue) */}
          <circle cx="23" cy="67" r="6" fill="#3B82F6" />
          <circle cx="23" cy="67" r="9" fill="#3B82F6" fillOpacity="0.2" />

          {/* Orbital Electron 2 (Top Right in Purple/Indigo) */}
          <circle cx="77" cy="27" r="6" fill="#7C3AED" />
          <circle cx="77" cy="27" r="9" fill="#7C3AED" fillOpacity="0.2" />

          {/* Center 'v' Mark */}
          <path
            d="M40 41L50 61L60 41"
            stroke={theme === 'dark' ? '#FFFFFF' : '#111827'}
            strokeWidth="6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>

      {/* Wordmark Typography */}
      {showWordmark && (
        <span
          className={`${wordmarkClasses} font-sans ${
            theme === 'dark' ? 'text-white' : 'text-slate-900'
          } tracking-[-0.03em]`}
        >
          Valence
        </span>
      )}
    </div>
  );
};
