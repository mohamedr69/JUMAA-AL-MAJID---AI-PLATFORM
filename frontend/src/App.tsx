import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ProtectedRoute, RoleRoute } from "./components/ProtectedRoute";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { AccessDeniedPage } from "./pages/AccessDeniedPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { CreateProjectPage } from "./pages/CreateProjectPage";
import { LoginPage } from "./pages/LoginPage";
import { OpenProjectPage } from "./pages/OpenProjectPage";
import { OpeningScreen } from "./pages/OpeningScreen";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";

function LoginRoute() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Navigate to="/" replace />;
  return <LoginPage />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/access-denied" element={<AccessDeniedPage />} />

        <Route
          element={
            <ProtectedRoute>
              <AppShell />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<OpeningScreen />} />
          <Route
            path="/projects/new"
            element={
              <RoleRoute roles={["admin", "design_manager", "design_engineer"]}>
                <CreateProjectPage />
              </RoleRoute>
            }
          />
          <Route path="/projects" element={<OpenProjectPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route
            path="/admin/users"
            element={
              <RoleRoute roles={["admin"]}>
                <AdminUsersPage />
              </RoleRoute>
            }
          />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
