import cv2
import numpy as np
import tensorflow as tf

import config
import util

############################################################################################################
#                       seg_gt calculation                                                                 #
############################################################################################################

def anchor_rect_height_ratio(anchor, rect):
    """calculate the height ratio between anchor and rect
    """
    rect_height = min(rect[2], rect[3])
    anchor_height = anchor[2] * 1.0
    ratio = anchor_height / rect_height
    return max(ratio, 1.0 / ratio)
    
def is_anchor_center_in_rect(anchor, xs, ys, bbox_idx):
    """tell if the center of the anchor is in the rect represented using xs and ys and bbox_idx 
    """
    bbox_points = zip(xs[bbox_idx, :], ys[bbox_idx, :])
    cnt = util.img.points_to_contour(bbox_points);
    acx, acy, aw, ah = anchor
    return util.img.is_in_contour((acx, acy), cnt)
    
def min_area_rect(xs, ys):
    """
    xs: numpy ndarray of shape N*4, [x1, x2, x3, x4]
    ys: numpy ndarray of shape N*4, [y1, y2, y3, y4]
    return the oriented rects sorrounding the box represented by [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
    """
    xs = np.asarray(xs, dtype = np.float32)
    ys = np.asarray(ys, dtype = np.float32)
        
    num_rects = xs.shape[0]
    box = np.empty((num_rects, 5))#cx, cy, w, h, theta
    for idx in xrange(num_rects):
        points = zip(xs[idx, :], ys[idx, :])
        cnt = util.img.points_to_contour(points)
        rect = cv2.minAreaRect(cnt)
        cx, cy = rect[0]
        w, h = rect[1]
        theta = rect[2]
        box[idx, :] = [cx, cy, w, h, theta]
    
    box = np.asarray(box, dtype = xs.dtype)
    return box
    

def transform_cv_rect(rects):
    """Transform the rects from opencv method minAreaRect to our rects. 
    Step 1 of Figure 5 in seglink paper
    rects: (5, ) or (N, 5)
    """
    only_one = False
    if len(np.shape(rects)) == 1:
        rects = np.expand_dims(rects, axis = 0)
        only_one = True
    assert np.shape(rects)[1] == 5, 'The shape of rects must be (N, 5), but meet %s'%(str(np.shape(rects)))
    rects = np.asarray(rects, dtype = np.float32).copy()
    num_rects = np.shape(rects)[0]
    for idx in xrange(num_rects):
        cx, cy, w, h, theta = rects[idx, ...];
        #assert theta < 0 and theta >= -90, "invalid theta: %f"%(theta) 
        if abs(theta) > 45 or (abs(theta) == 45 and w < h):
            w, h = [h, w]
            theta = 90 + theta
        rects[idx, ...] = [cx, cy, w, h, theta]
    if only_one:
        return rects[0, ...]
    return rects                
    

def rotate_oriented_bbox_to_horizontal(center, bbox):
    """
    center: the center of rotation
    bbox: [cx, cy, w, h, theta]
    Step 2 of Figure 5 in seglink paper
    """
    assert np.shape(center) == (2, ), "center must be a vector of length 2"
    assert np.shape(bbox) == (5, ) or np.shape(bbox) == (4, ), "bbox must be a vector of length 4 or 5"
    bbox = np.asarray(bbox.copy(), dtype = np.float32)
    
    cx, cy, w, h, theta = bbox[...];
    M = cv2.getRotationMatrix2D(center, theta, scale = 1) # 2x3
    
    cx, cy = np.dot(M, np.transpose([cx, cy, 1]))
    
    bbox[0:2] = [cx, cy]
    return bbox

def crop_horizontal_bbox_using_anchor(bbox, anchor):
    """Step 3 in Figure 5 in seglink paper
    """
    assert np.shape(anchor) == (4, ), "anchor must be a vector of length 4"
    assert np.shape(bbox) == (5, ) or np.shape(bbox) == (4, ), "bbox must be a vector of length 4 or 5"
    acx, acy, aw, ah = anchor
    axmin = acx - aw / 2.0;
    axmax = acx + aw / 2.0;
    
    cx, cy, w, h = bbox[0:4]
    xmin = cx - w / 2.0
    xmax = cx + w / 2.0
    
    xmin = max(xmin, axmin)
    xmax = min(xmax, axmax)
    
    cx = (xmin + xmax) / 2.0;
    w = xmax - xmin
    bbox = bbox.copy()
    bbox[0:4] = [cx, cy, w, h]
    return bbox

def rotate_horizontal_bbox_to_oriented(center, bbox):
    """
    center: the center of rotation
    bbox: [cx, cy, w, h, theta]
    Step 4 of Figure 5 in seglink paper
    """
    assert np.shape(center) == (2, ), "center must be a vector of length 2"
    assert np.shape(bbox) == (5, ) or np.shape(bbox) == (4, ), "bbox must be a vector of length 4 or 5"
    bbox = np.asarray(bbox.copy(), dtype = np.float32)
    
    cx, cy, w, h, theta = bbox[...];
    M = cv2.getRotationMatrix2D(center, -theta, scale = 1) # 2x3
    cx, cy = np.dot(M, np.transpose([cx, cy, 1]))
    bbox[0:2] = [cx, cy]
    return bbox


def cal_seg_gt_for_single_anchor(anchor, rect):
    # rotate text box along the center of anchor to horizontal direction
    center = (anchor[0], anchor[1])
    rect = rotate_oriented_bbox_to_horizontal(center, rect)

    # crop horizontal text box to anchor    
    rect = crop_horizontal_bbox_using_anchor(rect, anchor)
    
    # rotate the box to original direction
    rect = rotate_horizontal_bbox_to_oriented(center, rect)
    return rect    
    

def match_anchor_to_text_boxes(anchors, xs, ys):
    """Match anchors to text boxes. 
       The match results are stored in a vector, each of whose is the index of matched box if >=0, and returned.
    """
    
    assert len(np.shape(anchors)) == 2 and np.shape(anchors)[1] == 4, "the anchors must be a tensor with shape = (num_anchors, 4)"
    assert len(np.shape(xs)) == 2 and np.shape(xs) == np.shape(ys) and np.shape(ys)[1] == 4, "the xs, ys must be a tensor with shape = (num_bboxes, 4)"
    anchors = np.asarray(anchors, dtype = np.float32)
    xs = np.asarray(xs, dtype = np.float32)
    ys = np.asarray(ys, dtype = np.float32)
    
    num_anchors = anchors.shape[0]
    labels = np.ones((num_anchors, ), dtype = np.int32) * -1;
    seg_gt = np.zeros((num_anchors, 5), dtype = np.float32)
    num_bboxes = xs.shape[0]
    
    #represent bboxes with min area rects
    rects = min_area_rect(xs, ys) # shape = (num_bboxes, 5)
    rects = transform_cv_rect(rects)
    assert rects.shape == (num_bboxes, 5)
    
    #represent bboxes with contours
    cnts = []
    for bbox_idx in xrange(num_bboxes):
        bbox_points = zip(xs[bbox_idx, :], ys[bbox_idx, :])
        cnt = util.img.points_to_contour(bbox_points);
        cnts.append(cnt)
    # match
    for anchor_idx in xrange(num_anchors):
        anchor = anchors[anchor_idx, :]
        acx, acy, aw, ah = anchor
        
        center_point_matched = False
        height_matched = False
        for bbox_idx in xrange(num_bboxes):
            # center point check

            center_point_matched = util.img.is_in_contour((acx, acy), cnts[bbox_idx])
            if not center_point_matched:
                continue
                
            # height ratio check
            rect = rects[bbox_idx, :]
            cx, cy, w, h = rect[0:4]
            height = min(w, h);
            ratio = aw / height # aw == ah
            height_matched = max(ratio, 1/ratio) <= config.max_height_ratio
            
            if height_matched and center_point_matched:
                # an anchor can only be matched to at most one bbox
                labels[anchor_idx] = bbox_idx
                seg_gt[anchor_idx, :] = cal_seg_gt_for_single_anchor(anchor, rect)
                
    return labels, seg_gt



############################################################################################################
#                       link_gt calculation                                                                #
############################################################################################################

def reshape_link_gt_by_layer(link_gt):
    inter_layer_link_gts = {}
    cross_layer_link_gts = {}
    
    idx = 0;
    for layer_idx, layer_name in enumerate(config.feat_layers):
        layer_shape = config.feat_shapes[layer_name]
        lh, lw = layer_shape
        
        length = lh * lw * 8;
        layer_link_gt = link_gt[idx: idx + length]
        idx = idx + length;
        layer_link_gt = np.reshape(layer_link_gt, (lh, lw, 8))
        inter_layer_link_gts[layer_name] = layer_link_gt
        
    for layer_idx in xrange(1, len(config.feat_layers)):
        layer_name = config.feat_layers[layer_idx]
        layer_shape = config.feat_shapes[layer_name]
        lh, lw = layer_shape
        length = lh * lw * 4;
        layer_link_gt = link_gt[idx: idx + length]
        idx = idx + length;
        layer_link_gt = np.reshape(layer_link_gt, (lh, lw, 4))
        cross_layer_link_gts[layer_name] = layer_link_gt
    
    assert idx == len(link_gt)
    return inter_layer_link_gts, cross_layer_link_gts
        
def reshape_labels_by_layer(labels):
    layer_labels = {}
    idx = 0;
    for layer_name in config.feat_layers:
        layer_shape = config.feat_shapes[layer_name]
        label_length = np.prod(layer_shape)
        
        layer_match_result = labels[idx: idx + label_length]
        idx = idx + label_length;
        
        layer_match_result = np.reshape(layer_match_result, layer_shape)
        
        layer_labels[layer_name] = layer_match_result;
    assert idx == len(labels)
    return layer_labels;

def get_inter_layer_neighbours(x, y):
    return [(x - 1, y - 1), (x, y - 1), (x + 1, y - 1), \
            (x - 1, y),                 (x + 1, y),  \
            (x - 1, y + 1), (x, y + 1), (x + 1, y + 1)]
    
def get_cross_layer_neighbours(x, y):
    return [(2 * x, 2 * y), (2 * x + 1, 2 * y), (2 * x, 2 * y + 1), (2 * x + 1, 2 * y + 1)]
    
def is_valid_cord(x, y, w, h):
    return x >=0 and x < w and y >= 0 and y < h;

def cal_link_gt(labels):
    layer_labels = reshape_labels_by_layer(labels)
    inter_layer_link_gts = []
    cross_layer_link_gts = []
    for layer_idx, layer_name in enumerate(config.feat_layers):
        layer_match_result = layer_labels[layer_name]
        h, w = config.feat_shapes[layer_name]
        
        inter_layer_link_gt = np.zeros((h, w, 8), dtype = np.int32)
        
        if layer_idx > 0:
            cross_layer_link_gt = np.zeros((h, w, 4), dtype = np.int32)
            
        for x in xrange(w):
            for y in xrange(h):
                if layer_match_result[y, x] >= 0:
                    matched_idx = layer_match_result[y, x]
                    
                    # inter layer
                    neighbours = get_inter_layer_neighbours(x, y)
                    for nidx, nxy in enumerate(neighbours):
                        nx, ny = nxy
                        if is_valid_cord(nx, ny, w, h):
                            n_matched_idx = layer_match_result[ny, nx]
                            if matched_idx == n_matched_idx:
                                inter_layer_link_gt[y, x, nidx] = 1;
                                
                    # cross layer
                    if layer_idx > 0:
                        previous_layer_name = config.feat_layers[layer_idx - 1];
                        ph, pw = config.feat_shapes[previous_layer_name]
                        previous_layer_match_result = layer_labels[previous_layer_name]
                        neighbours = get_cross_layer_neighbours(x, y)
                        for nidx, nxy in enumerate(neighbours):
                            nx, ny = nxy
                            if is_valid_cord(nx, ny, pw, ph):
                                n_matched_idx = previous_layer_match_result[ny, nx]
                                if matched_idx == n_matched_idx:
                                    cross_layer_link_gt[y, x, nidx] = 1;                             
                    
        inter_layer_link_gts.append(inter_layer_link_gt)
        
        if layer_idx > 0:
            cross_layer_link_gts.append(cross_layer_link_gt)
    
    inter_layer_link_gts = np.hstack([np.reshape(t, -1) for t in inter_layer_link_gts]);
    cross_layer_link_gts = np.hstack([np.reshape(t, -1) for t in cross_layer_link_gts]);
    link_gt = np.hstack([inter_layer_link_gts, cross_layer_link_gts])
    return link_gt


def get_all_seglink_gt(xs, ys, normalize = False):
    anchors = config.default_anchors
    labels, seg_gt = match_anchor_to_text_boxes(anchors, xs, ys);
    link_gt = cal_link_gt(labels);
    if normalize:    
        # normalize the segment ground truth between 1 and 0.
        h_I, w_I = config.image_shape
        seg_gt = np.asarray(seg_gt, dtype = np.float32) / [w_I, h_I, w_I, h_I, 1.0]
    
    labels = np.asarray(labels >=0, dtype = np.int32);
    seg_gt = np.asarray(seg_gt, dtype = np.float32)
    link_gt = np.asarray(link_gt, dtype = np.int32)
    return labels, seg_gt, link_gt
    

def tf_get_all_seglink_gt(xs, ys):
    labels, seg_gt, link_gt = tf.py_func(get_all_seglink_gt, [xs, ys, True], [tf.int32, tf.float32, tf.int32]);
    labels.set_shape([config.num_anchors])
    seg_gt.set_shape([config.num_anchors, 5])
    link_gt.set_shape([config.num_links])
    return labels, seg_gt, link_gt;

############################################################################################################
#                       linking segments together                                                          #
############################################################################################################
def group_segs(seg_scores, link_scores):
    """
    group segments based on their scores and links.
    Return: segment groups as a list, consisting of list of segment indexes, reprensting a group of segments belonging to a same bbox.
    """
    seg_confidence_threshold = config.seg_confidence_threshold
    link_confidence_threshold = config.link_confidence_threshold
    
    assert len(np.shape(seg_scores)) == 1
    assert len(np.shape(link_scores)) == 1
    
    valid_segs = np.where(seg_scores >= seg_confidence_threshold)[0];
    assert valid_segs.ndim == 1
    mask = {}
    for s in valid_segs:
        mask[s] = -1;
    
    def get_root(idx):
        parent = mask[idx]
        while parent != -1:
            idx = parent
            parent = mask[parent]
        return idx
            
    def union(idx1, idx2):
        root1 = get_root(idx1)
        root2 = get_root(idx2)
        
        if root1 != root2:
            mask[root1] = root2
            
    def to_list():
        result = {}
        for idx in mask:
            root = get_root(idx)
            if root not in result:
                result[root] = []
            
            result[root].append(idx)
            
        return [result[root] for root in result]

        
    seg_indexes = np.arange(len(seg_scores))
    layer_seg_indexes = reshape_labels_by_layer(seg_indexes)

    layer_inter_link_scores, layer_cross_link_scores = reshape_link_gt_by_layer(link_scores)
    
    for layer_index, layer_name in enumerate(config.feat_layers):
        layer_shape = config.feat_shapes[layer_name]
        lh, lw = layer_shape
        layer_seg_index = layer_seg_indexes[layer_name]
        layer_inter_link_score = layer_inter_link_scores[layer_name]
        if layer_index > 0:
            previous_layer_name = config.feat_layers[layer_index - 1]
            previous_layer_seg_index = layer_seg_indexes[previous_layer_name]
            previous_layer_shape = config.feat_shapes[previous_layer_name]
            plh, plw = previous_layer_shape
            layer_cross_link_score = layer_cross_link_scores[layer_name]
            
            
        for y in xrange(lh):
            for x in xrange(lw):
                seg_index = layer_seg_index[y, x]
                
                _seg_score = seg_scores[seg_index]
                if _seg_score >= seg_confidence_threshold:

                    # find inter layer linked neighbours                    
                    inter_layer_neighbours = get_inter_layer_neighbours(x, y)
                    for nidx, nxy in enumerate(inter_layer_neighbours):
                        nx, ny = nxy
                        
                        # the condition of connecting neighbour segment: valid coordinate, 
                        # valid segment confidence and valid link confidence.
                        if is_valid_cord(nx, ny, lw, lh) and \
                            seg_scores[layer_seg_index[ny, nx]]  >= seg_confidence_threshold and \
                            layer_inter_link_score[y, x, nidx] >= link_confidence_threshold:
                            n_seg_index = layer_seg_index[ny, nx]
                            union(seg_index, n_seg_index)
                    
                    # find cross layer linked neighbours
                    if layer_index > 0:
                        cross_layer_neighbours = get_cross_layer_neighbours(x, y)
                        for nidx, nxy in enumerate(cross_layer_neighbours):
                            nx, ny = nxy
                            if is_valid_cord(nx, ny, plw, plh) and \
                               seg_scores[previous_layer_seg_index[ny, nx]]  >= seg_confidence_threshold and \
                               layer_cross_link_score[y, x, nidx] >= link_confidence_threshold:
                               
                                n_seg_index = previous_layer_seg_index[ny, nx]
                                union(seg_index, n_seg_index)
                                
    return to_list()
        
        
    
############################################################################################################
#                       combining segments to bboxes                                                       #
############################################################################################################
def seglink_to_bbox(seg_scores, link_scores, segs):
    seg_groups = group_segs(seg_scores, link_scores);
    bboxes = []
    for group in seg_groups:
        group = [segs[idx, :] for idx in group]
        bbox = combine_segs(group)
        bboxes.append(bbox)
    return np.asarray(bboxes)

def sin(theta):
    return np.sin(theta / 180.0 * np.pi)
def cos(theta):
    return np.cos(theta / 180.0 *  np.pi)
def tan(theta):
    return np.tan(theta / 180.0 * np.pi)
    
def combine_segs(segs):
    segs = np.asarray(segs)
    assert segs.ndim == 2
    assert segs.shape[-1] == 5    
    # find the best straight line fitting all center points: y = kx + b
    cxs = segs[:, 0]
    cys = segs[:, 1]

    ## the slope
    bar_theta = np.mean(segs[:, 4])# average theta
    k = tan(bar_theta);
    
    ## the bias: minimize sum (k*x_i + b - y_i)^2
    ### let c_i = k*x_i - y_i
    ### sum (k*x_i + b - y_i)^2 = sum(c_i + b)^2
    ###                           = sum(c_i^2 + b^2 + 2 * c_i * b)
    ###                           = n * b^2 + 2* sum(c_i) * b + sum(c_i^2)
    ### the target b = - sum(c_i) / n = - mean(c_i) = mean(y_i - k * x_i)
    b = np.mean(cys - k * cxs)
    
    # find the projections of all centers on the straight line
    ## firstly, move both the line and centers upward by distance b, so as to make the straight line crossing the point(0, 0): y = kx
    ## reprensent the line as a vector (1, k), and the projection of vector(x, y) on (1, k) is: proj = (x + k * y)  / sqrt(1 + k^2)
    ## the projection point of (x, y) on (1, k) is (proj * cos(theta), proj * sin(theta))
    t_cys = cys - b
    projs = (cxs + k * t_cys) / np.sqrt(1 + k**2)
    proj_points = np.transpose([projs * cos(bar_theta), projs * sin(bar_theta)])
    
    # find the max distance
    max_dist = 0;
    idx1 = -1;
    idx2 = -1;

    for i in xrange(len(proj_points)):
        point1 = proj_points[i, :]
        for j in xrange(i + 1, len(proj_points)):
            point2 = proj_points[j, :]
            dist = np.sqrt(np.sum((point1 - point2) ** 2))
            if dist > max_dist:
                idx1 = i
                idx2 = j
                max_dist = dist
    assert idx1 >= 0 and idx2 >= 0
    # the bbox: bcx, bcy, bw, bh, average_theta
    seg1 = segs[idx1, :]
    seg2 = segs[idx2, :]
    bcx, bcy = (seg1[:2] + seg2[:2]) / 2.0
    bh = np.mean(segs[:, 3])
    bw = max_dist + (seg1[2] + seg2[2]) / 2.0
    return bcx, bcy, bw, bh, bar_theta, b
            