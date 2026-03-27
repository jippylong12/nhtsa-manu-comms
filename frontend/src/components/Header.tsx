/* Header Component */

import { Car, Github } from 'lucide-react';
import styles from './Header.module.css';

export function Header() {
    return (
        <header className={styles.header}>
            <div className={styles.headerContent}>
                <div className={styles.logo}>
                    <div className={styles.logoIcon}>
                        <Car size={28} />
                    </div>
                    <div>
                        <h1 className={styles.logoTitle}>NHTSA Comms</h1>
                        <span className={styles.logoSubtitle}>Manufacturer Communications Tracker</span>
                    </div>
                </div>
                <nav className={styles.navLinks}>
                    <a
                        href="https://api.nhtsa.gov"
                        target="_blank"
                        rel="noopener noreferrer"
                        className={styles.navLink}
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
        </header>
    );
}
