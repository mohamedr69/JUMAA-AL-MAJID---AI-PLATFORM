import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ProtectedRoute, RoleRoute } from "./components/ProtectedRoute";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { AccessDeniedPage } from "./pages/AccessDeniedPage";
import { AccountPage } from "./pages/AccountPage";
import { AdminEquipmentCurrentsPage } from "./pages/AdminEquipmentCurrentsPage";
import { AdminKnowledgePage } from "./pages/AdminKnowledgePage";
import { AdminSystemPage } from "./pages/AdminSystemPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { CreateProjectPage } from "./pages/CreateProjectPage";
import { LoginPage } from "./pages/LoginPage";
import { OpenProjectPage } from "./pages/OpenProjectPage";
import { OpeningScreen } from "./pages/OpeningScreen";
import { ProjectBatteryPage } from "./pages/ProjectBatteryPage";
import { ProjectAmplifierPage } from "./pages/ProjectAmplifierPage";
import { ProjectPowerPage } from "./pages/ProjectPowerPage";
import { ProjectBoqPage } from "./pages/ProjectBoqPage";
import { ProjectProposedMaterialsPage } from "./pages/ProjectProposedMaterialsPage";
import { ProjectCalculationsPage } from "./pages/ProjectCalculationsPage";
import { ProjectCompliancePage } from "./pages/ProjectCompliancePage";
import { ProjectBoqRevisionsPage } from "./pages/ProjectBoqRevisionsPage";
import { ProjectBoqRereadPage } from "./pages/ProjectBoqRereadPage";
import { ProjectDocumentsPage } from "./pages/ProjectDocumentsPage";
import { ProjectHomePage } from "./pages/ProjectHomePage";
import { ProjectInfoPage } from "./pages/ProjectInfoPage";
import { ProjectMaterialSubmittalPage } from "./pages/ProjectMaterialSubmittalPage";
import { ProjectLogsPage } from "./pages/ProjectLogsPage";
import { ProjectDrawingsPage } from "./pages/ProjectDrawingsPage";
import { UnderMaintenance } from "./components/UnderMaintenance";
// ProjectVoiceEvacuationPage is deliberately not imported: the Amplifier tab
// is marked "soon". The page is kept in src/pages for when it is released.
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
          <Route path="/account" element={<AccountPage />} />
          <Route path="/projects/:id" element={<ProjectWorkspace />}>
            <Route index element={<ProjectHomePage />} />
            <Route path="info" element={<ProjectInfoPage />} />
            <Route path="boq" element={<ProjectBoqPage />} />
            <Route path="materials" element={<ProjectProposedMaterialsPage />} />
            <Route path="boq/revisions" element={<ProjectBoqRevisionsPage />} />
            <Route path="boq/reread" element={<ProjectBoqRereadPage />} />
            <Route path="calculations" element={<ProjectCalculationsPage />}>
              <Route index element={<Navigate to="battery" replace />} />
              <Route path="battery" element={<ProjectBatteryPage />} />
              <Route path="amplifier" element={<ProjectAmplifierPage />} />
              <Route path="power" element={<ProjectPowerPage />} />
            </Route>
            <Route path="compliance" element={<ProjectCompliancePage />} />
            <Route path="drawings" element={<ProjectDrawingsPage />} />
            {/* Where these pages used to live. */}
            <Route path="batteries" element={<Navigate to="../calculations/battery" replace />} />
            <Route path="design/ve" element={<Navigate to="../calculations/amplifier" replace />} />
            <Route path="submittal" element={<ProjectMaterialSubmittalPage />} />
            <Route path="documents" element={<ProjectDocumentsPage />} />
            {/* Sections of the design still being built: each says so. */}
            <Route path="logs" element={<ProjectLogsPage />} />
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
          <Route
            path="/admin/users/:userId"
            element={
              <RoleRoute roles={["admin"]}>
                <AccountPage />
              </RoleRoute>
            }
          />
          <Route
            path="/admin/knowledge"
            element={
              <RoleRoute roles={["admin"]}>
                <AdminKnowledgePage />
              </RoleRoute>
            }
          />
          <Route
            path="/admin/system"
            element={
              <RoleRoute roles={["admin"]}>
                <AdminSystemPage />
              </RoleRoute>
            }
          />
          <Route
            path="/admin/equipment-currents"
            element={
              <RoleRoute roles={["admin", "design_manager", "design_engineer"]}>
                <AdminEquipmentCurrentsPage />
              </RoleRoute>
            }
          />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
