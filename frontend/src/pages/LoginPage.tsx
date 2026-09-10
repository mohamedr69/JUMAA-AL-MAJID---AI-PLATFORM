import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { branding, heroFeatures } from "../branding";
import { BrandMark } from "../components/BrandMark";
import { CitySkylineBackdrop } from "../components/CitySkylineBackdrop";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../lib/api";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      const redirectTo = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(redirectTo, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen w-full">
      {/* Left hero panel */}
      <div className="relative hidden w-1/2 overflow-hidden bg-gradient-to-br from-navy-950 to-navy-900 lg:flex lg:flex-col lg:justify-between lg:p-12">
        <CitySkylineBackdrop />

        <div className="relative flex items-center gap-2 text-xs font-medium tracking-widest text-white/70">
          <span>DESIGN</span>
          <span className="text-white/30">|</span>
          <span>BUILD</span>
          <span className="text-white/30">|</span>
          <span>DELIVER</span>
        </div>

        <div className="relative">
          <h1 className="text-4xl font-bold leading-tight text-white">
            {branding.heroHeadline[0]}
            <br />
            <span className="bg-gradient-to-r from-white to-brand-100 bg-clip-text text-transparent">
              {branding.heroHeadline[1]}
            </span>
          </h1>
          <p className="mt-3 text-white/70">{branding.heroSubtext}</p>

          <div className="mt-10 grid grid-cols-1 gap-4">
            {heroFeatures.map((f) => (
              <div key={f.title} className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/10">
                  <span className="h-2 w-2 rounded-full bg-brand-500" />
                </div>
                <div>
                  <div className="text-sm font-semibold text-white">{f.title}</div>
                  <div className="text-xs text-white/60">{f.subtitle}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative text-xs tracking-widest text-white/50">
          SAFETY | SECURITY | SUSTAINABILITY
        </div>
      </div>

      {/* Right form panel */}
      <div className="flex w-full flex-col justify-between bg-white p-6 lg:w-1/2 lg:p-16">
        <div className="flex justify-end text-sm text-gray-500">English</div>

        <div className="mx-auto w-full max-w-sm">
          <div className="mb-8 flex flex-col items-center text-center">
            <BrandMark size={48} />
            <div className="mt-3 text-lg font-bold tracking-tight text-navy-900">
              {branding.appName.toUpperCase()}
            </div>
            <div className="text-xs tracking-widest text-gray-400">{branding.tagline.toUpperCase()}</div>
          </div>

          <h2 className="text-xl font-bold text-navy-900">Welcome Back</h2>
          <p className="mt-1 text-sm text-gray-500">Sign in to your account</p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <div>
              <label htmlFor="email" className="mb-1 block text-sm font-medium text-gray-700">
                Username or Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Enter your username or email"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">
                Password
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 pr-10 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  className="absolute inset-y-0 right-0 flex items-center px-3 text-xs text-gray-400 hover:text-gray-600"
                  tabIndex={-1}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            {error && (
              <div role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            )}

            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-2 text-gray-600">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                />
                Remember me
              </label>
              <span className="cursor-not-allowed text-brand-600 opacity-60" title="Not available yet">
                Forgot password?
              </span>
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-60"
            >
              {submitting ? "Signing in..." : "Sign In"}
            </button>
          </form>

          <div className="mt-6 flex items-center gap-3 text-xs text-gray-400">
            <div className="h-px flex-1 bg-gray-200" />
            or continue with
            <div className="h-px flex-1 bg-gray-200" />
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <button
              type="button"
              disabled
              title="Not enabled yet"
              className="flex items-center justify-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-500 opacity-60"
            >
              Microsoft
            </button>
            <button
              type="button"
              disabled
              title="Not enabled yet"
              className="flex items-center justify-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-500 opacity-60"
            >
              Google
            </button>
          </div>

          <p className="mt-6 text-center text-sm text-gray-500">
            Don&apos;t have an account?{" "}
            <span className="font-medium text-brand-600">Contact your administrator</span>
          </p>
        </div>

        <div className="flex items-center justify-between text-xs text-gray-400">
          <div className="flex items-center gap-2">
            <BrandMark size={20} />
            <span>{branding.companyName}</span>
          </div>
          <span>{branding.version}</span>
        </div>
      </div>
    </div>
  );
}
