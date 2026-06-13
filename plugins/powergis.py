import logging
import time
import re
import json
import powerplan
import os.path
from powerplan.diagram import to_dot
from powerplan.bom import generate_bom_html, generate_bom_csvs
from powerplan.test_schedules import generate_schedule_html
from collections import namedtuple
from sqlalchemy.sql import text

Connection = namedtuple("Connection", ["name", "I", "phases"])
Distro = namedtuple("Distro", ["fid", "type", "name", "load", "geom"])
Generator = namedtuple("Generator", ["fid", "type", "name", "geom"])


def get_key(row, name):
    if name in row:
        return row[name]
    return None


class PowerGIS():
    BUFFER = 1

    def __init__(self, db, _config = None, opts = None):
        self.log = logging.getLogger(__name__)

        self.db = db # needs to be a Engine connection
        self.opts = opts
        self.generator_layer = None
        self.distro_layer = None


        self.generators = {}
        self.distros = {}


    def get_generators(self):
        for row in self.db.execute(
            text("select id, type, name, ST_AsText(ST_Transform(geom, 4326)) as geom from emf2026.power_generator"),
        ):
            g = Generator(
                row.id, row.type, row.name, row.geom
            )
            yield g

    def get_distros(self):
        for row in self.db.execute(
            text("select id, type, name, load_kw, ST_AsText(ST_Transform(geom, 4326)) as geom from emf2026.power_distro"),
        ):
            if row.type==None or row.load_kw==None:
                log.error(f"Required parameter missing: id:{row.id} name:{row.name} type:{row.type} low_kw:{row.load_kw} geom:{row.geom}")
            d =  Distro(
                row.id, row.type, row.name, (row.load_kw * 1000), row.geom
            )
            yield d

    already_checked_outbound_connections = []

    def get_outbound_connections(self, ogc_fid):
        """Given the feature ID of a power network node, return all nodes which are connected to it,
        along with the layer that connection is in. An edge (cable) is deemed to be connected to a
        node if it ends within the "buffer" defined in self.BUFFER - this is in source CRS units
        which should be meters."""

        self.log.debug(f"Searching from node:{ogc_fid}")

        if ogc_fid in self.already_checked_outbound_connections:
            self.log.error(f"Loop detected via distro {ogc_fid}. \nTwo distros are within {2*self.BUFFER}m of eachother!")
            exit()
        self.already_checked_outbound_connections.append(ogc_fid)

        sql = text(
            """
            WITH start_point AS (
                -- Get the single starting geometry first (Lightning fast)
                SELECT geom FROM emf2026.power_distro WHERE id = :start_fid
                UNION ALL
                SELECT geom FROM emf2026.power_generator WHERE id = :start_fid
            ),
            found_edges AS (
                -- Find edges that touch that start point
                SELECT edge.*,
                    -- Determine which end of the cable is the "other" end
                    CASE
                        WHEN ST_DWithin(s.geom, ST_StartPoint(edge.geom), :buf) THEN ST_EndPoint(edge.geom)
                        ELSE ST_StartPoint(edge.geom)
                    END as other_end_geom
                FROM emf2026.power_cable AS edge
                CROSS JOIN start_point s
                WHERE ST_DWithin(s.geom, edge.geom, :buf) -- Uses GIST index on edge.geom (entire line)
                AND ( -- Can't just be a line passing a distro and not connecting
                    ST_Distance(s.geom, ST_StartPoint(edge.geom)) <= :buf -- Distro + start of line check
                    OR
                    ST_Distance(s.geom, ST_EndPoint(edge.geom)) <= :buf   -- Distro + end of line check
                )
            ),
            nodes_pool AS (
                -- Create a unified pool for the end node lookup
                SELECT id, geom FROM emf2026.power_distro
                UNION ALL
                SELECT id, geom FROM emf2026.power_generator
            )
            SELECT
                n.id,
                e.phases,
                e.amps,
                round(ST_Length(e.geom)::NUMERIC, 1) AS length
            FROM found_edges e
            JOIN nodes_pool n ON ST_DWithin(n.geom, e.other_end_geom, :buf);
            """
        )
        for row in self.db.execute(
            sql,
            {
                "start_fid": ogc_fid,
                "buf": self.BUFFER
            }
        ):
            self.log.debug(f"Found connection to node:{row.id} cable:{f"{row.amps}-{row.phases}"} len: {row.length}")
            yield row.id, row.amps, row.phases, row.length

    def generate_plan(self) -> powerplan.Plan:
        self.log.info("Getting spec")
        if self.opts.get("spec_dir"):
            spec = powerplan.EquipmentSpec(self.opts["spec_dir"])
        else:
            spec = None

        self.log.info(f"Got {len(spec)} spec")

        plan = powerplan.Plan(name=self.opts.get("name"), spec=spec)

        nodes = {}  # Index of all nodes
        tree_nodes = []  # List of initial nodes to traverse

        for gen in self.get_generators():
            node = powerplan.Generator(name=gen.name, type=gen.type, id=gen.fid, geom=gen.geom)
            plan.add_node(node)
            nodes[gen.fid] = node
            tree_nodes.append((node, gen.fid))
        
        numGenerators = len(nodes)
        self.log.info(f"Got {numGenerators} generators")

        for dist in self.get_distros():
            # FIXME: Better way to detect AMF panels
            if dist.type == "125AMF-EVENT":
                node = powerplan.AMF(name=dist.name, type=dist.type, id=dist.fid)
            else:
                node = powerplan.Distro(name=dist.name, type=dist.type.replace("-", " "), id=dist.fid, geom=dist.geom)
            plan.add_node(node)
            nodes[dist.fid] = node

            if dist.load:
                load = powerplan.Load(name=dist.name + " Load", load=dist.load)
                plan.add_node(load)
                plan.add_connection(node, load)

        numDistro = len(nodes) - numGenerators
        self.log.info(f"Got {numDistro} distros")

        self.log.info(f"Proceeding to map node graph (this may take some time)...")

        # Traverse the tree of nodes, starting with the set of generators.
        while len(tree_nodes) > 0:
            start_node, start_fid = tree_nodes.pop()

            for end_fid, amps, phases, length in self.get_outbound_connections(start_fid):
                end_node = nodes[end_fid]
                if plan.graph.has_edge(end_node, start_node):
                    # Don't follow the connection we just came from.
                    continue

                if type(start_node) == powerplan.AMF and amps > 63:
                    # If this is an AMF we don't want to continue the wrong
                    # way onto the other grid.
                    # FIXME: Work out a better way to deal with AMFs here
                    continue

                tree_nodes.append((end_node, end_fid))

                plan.add_connection(
                    start_node, end_node, amps, phases, length=length
                )

        return plan


    def export_plan_to_json(self, digraph):
        json_structure = []

        # Iterate through every device (node) in the graph
        for device_obj, node_data in digraph.nodes(data=True):

            distro_id = getattr(device_obj, 'id', str(device_obj))
            distro_name = getattr(device_obj, 'name', str(device_obj))
            device_type = getattr(type(device_obj), '__name__', str(device_obj))
            device_spec = device_obj.get_spec().get("ref") if device_obj.get_spec() else None
            device_geom = getattr(device_obj, 'geom', None)

            upstream_list = []
            downstream_list = []
            loads_list = []
            load_kw = None

            # 2. Find UPSTREAM connections (In-edges in a directed tree pointing toward us)
            for source, _, edge_data in digraph.in_edges(device_obj, data=True):
                # Extract cable details from edge data

                upstream_list.append({
                    "id": getattr(source, 'id', str(source)),
                    "name": getattr(source, 'name', str(source)),
                    "type": getattr(type(source), '__name__', str(source)),
                    "spec": source.get_spec().get("ref", str(source)),
                    "current": edge_data.get('current'),
                    "phases": edge_data.get('phases'),
                    "length": float(edge_data.get('length') or 0),
                    "cable_lengths": edge_data.get('cable_lengths')
                })


            # 3. Find DOWNSTREAM connections (Out-edges pointing away from us)
            for _, target, edge_data in digraph.out_edges(device_obj, data=True):

                if(type(target).__name__=="Load"):
                    load_kw = target.load().magnitude / 1000 if target.load().magnitude else 0
                    continue

                downstream_list.append({
                    "id": getattr(target, 'id', str(target)),
                    "name": getattr(target, 'name', str(target)),
                    "type": getattr(type(target), '__name__', str(target)),
                    "spec": target.get_spec().get("ref", str(target)),
                    "current": edge_data.get('current'),
                    "phases": edge_data.get('phases'),
                    "length": float(edge_data.get('length') or 0),
                    "cable_lengths": edge_data.get('cable_lengths')
                })

            # 4. Construct your exact schema mapping
            node_json = {
                "id": distro_id,
                "name": distro_name,
                "type": device_type,
                "geom": device_geom,
                "spec": device_spec,
                "load_kw": load_kw,
                "upstream": upstream_list,
                "downstream": downstream_list
            }

            json_structure.append(node_json)
            
        return json_structure


    def run(self):

        start = time.time()
        plan = self.generate_plan()
        self.log.info("Plan generated in %.2f seconds", time.time() - start)

        start = time.time()
        errors = plan.validate()
        if len(errors) > 0:
            self.log.warning("Power plan has validation errors:")
            for err in errors:
                self.log.warning("\t" + str(err))

        try:
            plan.generate()
        except Exception as e:
            self.log.exception("Error generating power plan: %s", e)
            return

        if plan.valid:
            self.log.info("Plan validated in %.2f seconds", time.time() - start)
        else:
            self.log.info("Plan validated with errors in %.2f seconds", time.time() - start)

        dot = to_dot(plan)

        out_path = self.opts.get("out_path", "power-plan-output")

        try:
            os.makedirs(out_path)
        except FileExistsError:
            pass

        start = time.time()

        self.log.info("Generating power-plan.pdf")
        with open(os.path.join(out_path, "power-plan.pdf"), "wb") as f:
            f.write(dot.create_pdf())

        for grid in plan.grids():
            dot = to_dot(grid, False)
            self.log.info(f"Generating power-plan-{grid.name}.pdf")
            with open(
                os.path.join(out_path, "power-plan-%s.pdf" % grid.name), "wb"
            ) as f:
                f.write(dot.create_pdf())

        self.log.info(f"Generating power-bom.html")
        with open(os.path.join(out_path, "power-bom.html"), "w") as f:
            try:
                f.write(generate_bom_html(plan))
            except ValueError as e:
                self.log.error(f"Could not generate BoM HTML: {e}")

        if plan.valid:
            self.log.info(f"Generating test-schedules.html")
            try:
                with open(os.path.join(out_path, "test-schedules.html"), "w") as f:
                    f.write(generate_schedule_html(plan))
            except ValueError as e:
                self.log.error(f"Could not generate test-schedule HTML: {e}")
            except Exception as e:
                self.log.exception("Error generating test schedules: %s", e)
        else:
            self.log.info("Skipping test schedule generation due to errors")

        if plan.valid:
            self.log.info(f"Generating powerplan_graph.json")
            try:
                output_json = self.export_plan_to_json(plan.graph)
                with open(os.path.join(out_path, "powerplan_graph.json"), "w") as f:
                    json.dump(output_json, f, indent=2)
            except ValueError as e:
                self.log.error(f"Could not generate JSON output: {e}")
            except Exception as e:
                self.log.exception("Error generating JSON: %s", e)
        else:
            self.log.info("Skipping output JSON generation due to errors")

        self.log.info(f"Generating cables-bom.csv")
        with open(os.path.join(out_path, "cables-bom.csv"), "w") as cables, open(
            os.path.join(out_path, "distros-bom.csv"), "w"
        ) as distros:
            try:
                generate_bom_csvs(plan, distros, cables)
            except ValueError as e:
                self.log.error(f"Could not generate BoM CSV: {e}") 

        # Debug - save the full plan for later analysis
        # with open(os.path.join(out_path, "power-plan.pyobj"), "wb") as f:
        #     import pickle
        #     pickle.dump(plan, f)

        self.log.info(f"Power plan outputs generated to [{out_path}] in {(time.time()-start):.2f} seconds")
