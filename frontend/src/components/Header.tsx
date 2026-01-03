/* Header Component */

import { Car, Github } from 'lucide-react';

export function Header() {
    return (
        <header className="header">
            <div className="container">
                <div className="header-content">
                    <div className="logo">
                        <div className="logo-icon">
                            <Car size={28} />
                        </div>
                        <div className="logo-text">
                            <h1>NHTSA Comms</h1>
                            <span>Manufacturer Communications Tracker</span>
                        </div>
                    </div>
                    <nav className="nav-links">
                        <a
                            href="https://api.nhtsa.gov"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="nav-link"
                        >
                            NHTSA API
                        </a>
                        <a
                            href="https://github.com"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="btn btn-ghost btn-icon"
                            title="View on GitHub"
                        >
                            <Github size={20} />
                        </a>
                    </nav>
                </div>
            </div>

            <style>{`
        .header {
          background: var(--bg-surface);
          border-bottom: 1px solid var(--border-subtle);
          padding: var(--space-md) 0;
          position: sticky;
          top: 0;
          z-index: 100;
          backdrop-filter: blur(12px);
        }

        .header-content {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: var(--space-lg);
        }

        .logo {
          display: flex;
          align-items: center;
          gap: var(--space-md);
        }

        .logo-icon {
          width: 48px;
          height: 48px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
          border-radius: var(--radius-md);
          color: white;
          box-shadow: 0 0 20px var(--color-primary-glow);
        }

        .logo-text h1 {
          font-size: 1.25rem;
          font-weight: 700;
          margin: 0;
          background: linear-gradient(135deg, var(--text-primary) 0%, var(--color-primary-light) 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }

        .logo-text span {
          font-size: 0.75rem;
          color: var(--text-muted);
        }

        .nav-links {
          display: flex;
          align-items: center;
          gap: var(--space-md);
        }

        .nav-link {
          font-size: 0.875rem;
          color: var(--text-secondary);
          transition: color var(--transition-fast);
        }

        .nav-link:hover {
          color: var(--text-primary);
        }

        @media (max-width: 640px) {
          .logo-text span {
            display: none;
          }
          .nav-link {
            display: none;
          }
        }
      `}</style>
        </header>
    );
}
