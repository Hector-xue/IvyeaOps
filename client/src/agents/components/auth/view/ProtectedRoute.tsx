import { Suspense, lazy, type ReactNode } from 'react';
import { IS_PLATFORM } from '../../../constants/config';
import { useAuth } from '../context/AuthContext';
import AuthLoadingScreen from './AuthLoadingScreen';
import LoginForm from './LoginForm';
import SetupForm from './SetupForm';

// 引导只在第一次进来时出现，之后再也不会渲染。
const Onboarding = lazy(() => import('../../onboarding/view/Onboarding'));

function LazyOnboarding({ onComplete }: { onComplete: () => void }) {
  return (
    <Suspense fallback={<AuthLoadingScreen />}>
      <Onboarding onComplete={onComplete} />
    </Suspense>
  );
}

type ProtectedRouteProps = {
  children: ReactNode;
};

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { user, isLoading, needsSetup, hasCompletedOnboarding, refreshOnboardingStatus } = useAuth();

  if (isLoading) {
    return <AuthLoadingScreen />;
  }

  if (IS_PLATFORM) {
    if (!hasCompletedOnboarding) {
      return <LazyOnboarding onComplete={refreshOnboardingStatus} />;
    }

    return <>{children}</>;
  }

  if (needsSetup) {
    return <SetupForm />;
  }

  if (!user) {
    return <LoginForm />;
  }

  if (!hasCompletedOnboarding) {
    return <LazyOnboarding onComplete={refreshOnboardingStatus} />;
  }

  return <>{children}</>;
}
