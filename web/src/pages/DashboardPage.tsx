import AlertsFeed from "../components/AlertsFeed";
import MeshDiagram from "../components/MeshDiagram";
import MeshLegend from "../components/MeshLegend";
import StatusTiles from "../components/StatusTiles";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <StatusTiles />
      <MeshDiagram />
      <MeshLegend />
      <AlertsFeed />
    </div>
  );
}
