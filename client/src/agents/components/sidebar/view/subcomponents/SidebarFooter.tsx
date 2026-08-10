import { ArrowUpCircle } from 'lucide-react';
import type { TFunction } from 'i18next';
import type { ReleaseInfo } from '../../../../types/sharedTypes';

type SidebarFooterProps = {
  updateAvailable: boolean;
  releaseInfo: ReleaseInfo | null;
  latestVersion: string | null;
  currentVersion: string;
  onShowVersionModal: () => void;
  t: TFunction;
};

export default function SidebarFooter({
  updateAvailable,
  releaseInfo,
  latestVersion,
  currentVersion,
  onShowVersionModal,
  t,
}: SidebarFooterProps) {
  return (
    <div className="flex-shrink-0" style={{ paddingBottom: 'env(safe-area-inset-bottom, 0)' }}>
      {/* Update banner */}
      {updateAvailable && (
        <>
          <div className="nav-divider" />
          {/* Desktop update */}
          <div className="hidden px-2 py-1.5 md:block">
            <button
              className="group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-blue-50/80 dark:hover:bg-blue-900/15"
              onClick={onShowVersionModal}
            >
              <div className="relative flex-shrink-0">
                <ArrowUpCircle className="h-4 w-4 text-blue-500 dark:text-blue-400" />
                <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
              </div>
              <div className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-blue-600 dark:text-blue-300">
                  {releaseInfo?.title || `v${latestVersion}`}
                </span>
                <span className="text-[length:var(--fs-10)] text-blue-500/70 dark:text-blue-400/60">
                  {t('version.updateAvailable')}
                </span>
              </div>
            </button>
          </div>

          {/* Mobile update */}
          <div className="px-3 py-2 md:hidden">
            <button
              className="flex h-11 w-full items-center gap-3 rounded-xl border border-blue-200/60 bg-blue-50/80 px-3.5 transition-all active:scale-[0.98] dark:border-blue-700/40 dark:bg-blue-900/15"
              onClick={onShowVersionModal}
            >
              <div className="relative flex-shrink-0">
                <ArrowUpCircle className="w-4.5 h-4.5 text-blue-500 dark:text-blue-400" />
                <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
              </div>
              <div className="min-w-0 flex-1 text-left">
                <span className="block truncate text-sm font-medium text-blue-600 dark:text-blue-300">
                  {releaseInfo?.title || `v${latestVersion}`}
                </span>
                <span className="text-xs text-blue-500/70 dark:text-blue-400/60">
                  {t('version.updateAvailable')}
                </span>
              </div>
            </button>
          </div>
        </>
      )}

    </div>
  );
}
