bl_info = {
    "name": "九宫格平面同步雕刻",
    "author": "OpenAI ChatGPT",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "视图3D > 侧栏 > 同步",
    "description": "创建一个细分平面，在雕刻任意九宫格中的一个格子时同步其他格子。",
    "category": "Object",
}

import math
import uuid
from collections import defaultdict

import bpy
from bpy.types import Operator, Panel
from bpy.props import IntProperty
from mathutils import Vector

SYNC_DATA = {}
IS_UPDATING = False

GROUP_ATTR_NAME = "sync_group_id"
ORIGIN_ATTR_NAME = "sync_tile_origin"


def ensure_attribute(mesh, name, attr_type, domain):
    attr = mesh.attributes.get(name)
    if attr:
        mesh.attributes.remove(attr)
    return mesh.attributes.new(name=name, type=attr_type, domain=domain)


def build_sync_cache(mesh):
    global SYNC_DATA

    verts = mesh.vertices
    if not verts:
        return None

    xs = [v.co.x for v in verts]
    ys = [v.co.y for v in verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x
    height = max_y - min_y
    if width == 0 or height == 0:
        return None

    cell_size_x = width / 3.0
    cell_size_y = height / 3.0

    group_attr = ensure_attribute(mesh, GROUP_ATTR_NAME, 'INT', 'POINT')
    origin_attr = ensure_attribute(mesh, ORIGIN_ATTR_NAME, 'FLOAT_VECTOR', 'POINT')

    groups = defaultdict(list)
    origins = []
    group_index_lookup = {}
    next_group_id = 0

    for idx, vert in enumerate(verts):
        co = vert.co
        tile_x = max(0, min(2, int(math.floor(((co.x - min_x) / cell_size_x) + 1e-6))))
        tile_y = max(0, min(2, int(math.floor(((co.y - min_y) / cell_size_y) + 1e-6))))

        tile_origin_x = min_x + tile_x * cell_size_x
        tile_origin_y = min_y + tile_y * cell_size_y
        tile_origin = Vector((tile_origin_x, tile_origin_y, 0.0))
        origins.append(tile_origin)
        origin_attr.data[idx].vector = tile_origin

        canon_x = round(co.x - tile_origin_x, 6)
        canon_y = round(co.y - tile_origin_y, 6)
        group_key = (canon_x, canon_y)
        if group_key not in group_index_lookup:
            group_index_lookup[group_key] = next_group_id
            next_group_id += 1
        group_id = group_index_lookup[group_key]
        group_attr.data[idx].value = group_id
        groups[group_id].append(idx)

    identifier = str(uuid.uuid4())
    mesh["sync_identifier"] = identifier

    cache = {
        "groups": list(groups.values()),
        "origins": origins,
    }
    SYNC_DATA[identifier] = cache
    return cache


def ensure_cache(mesh):
    identifier = mesh.get("sync_identifier")
    if identifier and identifier in SYNC_DATA:
        return SYNC_DATA[identifier]

    group_attr = mesh.attributes.get(GROUP_ATTR_NAME)
    origin_attr = mesh.attributes.get(ORIGIN_ATTR_NAME)
    if not group_attr or not origin_attr:
        return None

    groups = defaultdict(list)
    origins = []

    for idx in range(len(mesh.vertices)):
        group_id = group_attr.data[idx].value
        groups[group_id].append(idx)
        origins.append(Vector(origin_attr.data[idx].vector))

    if identifier is None:
        identifier = str(uuid.uuid4())
        mesh["sync_identifier"] = identifier

    cache = {
        "groups": list(groups.values()),
        "origins": origins,
    }
    SYNC_DATA[identifier] = cache
    return cache


def sync_sculpted_tiles(scene, depsgraph):
    global IS_UPDATING
    if IS_UPDATING:
        return

    context = bpy.context
    obj = context.active_object
    if not obj or obj.type != 'MESH':
        return
    if context.mode != 'SCULPT':
        return

    mesh = obj.data
    if not mesh.is_updated_data:
        return

    cache = ensure_cache(mesh)
    if not cache:
        return

    groups = cache["groups"]
    origins = cache["origins"]
    verts = mesh.vertices

    IS_UPDATING = True
    try:
        for group in groups:
            if len(group) <= 1:
                continue
            target = Vector((0.0, 0.0, 0.0))
            for idx in group:
                target += verts[idx].co - origins[idx]
            target /= len(group)
            for idx in group:
                verts[idx].co = origins[idx] + target
        mesh.update()
    finally:
        IS_UPDATING = False


class MESH_OT_create_sync_plane(Operator):
    bl_idname = "mesh.create_nine_tile_plane"
    bl_label = "新建"
    bl_description = "创建一个九宫格平面并启用同步雕刻"
    bl_options = {'REGISTER', 'UNDO'}

    subdivisions: IntProperty(
        name="细分",
        default=128,
        min=1,
        max=256,
        description="平面的细分次数"
    )

    def execute(self, context):
        bpy.ops.mesh.primitive_plane_add(size=3.0)
        obj = context.active_object
        if obj is None:
            self.report({'ERROR'}, "无法创建平面")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.subdivide(number_cuts=self.subdivisions)
        bpy.ops.object.mode_set(mode='OBJECT')

        cache = build_sync_cache(obj.data)
        if not cache:
            self.report({'ERROR'}, "初始化同步数据失败")
            return {'CANCELLED'}

        obj.name = "同步九宫格平面"
        obj.data.name = "同步九宫格网格"

        self.report({'INFO'}, "已创建九宫格同步平面")
        return {'FINISHED'}


class VIEW3D_PT_sync_plane_panel(Panel):
    bl_label = "九宫格同步"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "同步"

    def draw(self, context):
        layout = self.layout
        layout.operator(MESH_OT_create_sync_plane.bl_idname, text="新建")


def register():
    bpy.utils.register_class(MESH_OT_create_sync_plane)
    bpy.utils.register_class(VIEW3D_PT_sync_plane_panel)
    if sync_sculpted_tiles not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(sync_sculpted_tiles)


def unregister():
    if sync_sculpted_tiles in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(sync_sculpted_tiles)
    bpy.utils.unregister_class(VIEW3D_PT_sync_plane_panel)
    bpy.utils.unregister_class(MESH_OT_create_sync_plane)
    SYNC_DATA.clear()


if __name__ == "__main__":
    register()
