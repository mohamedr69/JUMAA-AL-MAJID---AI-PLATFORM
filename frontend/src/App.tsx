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
import { ProjectBatteryPage } from "./pages/ProjectBatteryPage";
import { ProjectBoqPage } from "./pages/ProjectBoqPage";
import { ProjectCalculationsPage } from "./pages/ProjectCalculationsPage";
import { ProjectCompliancePage } from "./pages/ProjectCompliancePage";
import { ProjectBoqRevisionsPage } from "./pages/ProjectBoqRevisionsPage";
import { ProjectDocumentsPage } from "./pages/ProjectDocumentsPage";
import { ProjectHomePage } from "./pages/ProjectHomePage";
import { ProjectInfoPage } from "./pages/ProjectInfoPage";
import { ProjectMaterialSubmittalPage } from "./pages/ProjectMaterialSubmittalPage";
import { UnderMaintenance } from "./components/UnderMaintenance";
import { ProjectVoiceEvacuationPage } from "./pages/ProjectVoiceEvacuationPage";
import { ProjectWorkspace } from "./pages/ProjectWorkspace";

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
          <Route path="/projects/:id" element={<ProjectWorkspace />}>
            <Route index element={<ProjectHomePage />} />
            <Route path="info" element={<ProjectInfoPage />} />
            <Route path="boq" element={<ProjectBoqPage />} />
            <Route path="boq/revisions" element={<ProjectBoqRevisionsPage />} />
            <Route path="calculations" element={<ProjectCalculationsPage />}>
              <Route index element={<Navigate to="battery" replace />} />
              <Route path="battery" element={<ProjectBatteryPage />} />
              <Route path="amplifier" element={<ProjectVoiceEvacuationPage />} />
              <Route
                path="power"
                element={<UnderMaintenance title="Power calculation" note="Panel and auxiliary power supply sizing sits here once it is built." />}
              />
            </Route>
            <Route path="compliance" element={<ProjectCompliancePage />} />
            {/* Where these pages used to live. */}
            <Route path="batteries" element={<Navigate to="../calculations/battery" replace />} />
            <Route path="design/ve" element={<Navigate to="../calculations/amplifier" replace />} />
            <Route path="submittal" element={<ProjectMaterialSubmittalPage />} />
            <Route path="documents" element={<ProjectDocumentsPage />} />
            {/* Sections of the design still being built: each says so. */}
            <Route path="drawings" element={<UnderMaintenance title="Drawings" />} />
            <Route path="queries" element={<UnderMaintenance title="Technical Queries" />} />
            <Route path="logs" element={<UnderMaintenance title="Logs" />} />
            <Route path="om-manual" element={<UnderMaintenance title="O&M Manual" />} />
            <Route path="reports" element={<UnderMaintenance title="Reports" />} />
            <Route path="team" element={<UnderMaintenance title="Project Team" />} />
            <Route path="settings" element={<UnderMaintenance title="Project Settings" />} />
          </Route>
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
