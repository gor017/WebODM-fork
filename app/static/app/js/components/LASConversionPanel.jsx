import React from 'react';
import { _ } from '../classes/gettext';
import csrf from '../django/csrf';

export default class LASConversionPanel extends React.Component {
    constructor(props) {
        super(props);
        this.state = {
            file: null,
            mode: 'rgb',
            resolution: 0.1,
            multiview: false,
            tileSize: 100,
            overlap: 0.3,
            convertToJpg: true,
            uploading: false,
            progress: 0,
            result: null,
            error: null
        };
        
        this.handleFileChange = this.handleFileChange.bind(this);
        this.handleSubmit = this.handleSubmit.bind(this);
        this.handleDownload = this.handleDownload.bind(this);
    }
    
    handleFileChange(e) {
        this.setState({ file: e.target.files[0] });
    }
    
    handleDownload(url, filename) {
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }
    
    async handleSubmit(e) {
        e.preventDefault();
        
        if (!this.state.file) {
            this.setState({ error: _("Please select a LAS/LAZ file") });
            return;
        }
        
        this.setState({ uploading: true, progress: 0, error: null, result: null });
        
        try {
            const formData = new FormData();
            formData.append('file', this.state.file);
            formData.append('mode', this.state.mode);
            formData.append('resolution', this.state.resolution);
            formData.append('multiview', this.state.multiview ? 'true' : 'false');
            formData.append('tile_size', this.state.tileSize);
            formData.append('overlap', this.state.overlap);
            formData.append('convert_to_jpg', this.state.convertToJpg ? 'true' : 'false');
            
            const response = await fetch('/api/las-convert/', {
                method: 'POST',
                headers: {
                    [csrf.header]: csrf.token
                },
                body: formData
            });
            
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.detail || data.message || _("Conversion failed"));
            }
            
            this.setState({
                uploading: false,
                result: data,
                progress: 100
            });
            
        } catch (error) {
            this.setState({
                uploading: false,
                error: error.message || _("Conversion failed"),
                progress: 0
            });
        }
    }
    
    render() {
        const { file, mode, resolution, multiview, tileSize, overlap, 
                convertToJpg, uploading, progress, result, error } = this.state;
        
        return (
            <div className="las-conversion-panel">
                <h3>{_("Convert LAS/LAZ to Images")}</h3>
                
                <form onSubmit={this.handleSubmit}>
                    <div className="form-group">
                        <label>{_("LAS/LAZ File")}</label>
                        <input
                            type="file"
                            className="form-control"
                            accept=".las,.laz"
                            onChange={this.handleFileChange}
                            disabled={uploading}
                        />
                        {file && <small className="text-muted">{file.name}</small>}
                    </div>
                    
                    <div className="form-group">
                        <label>{_("Mode")}</label>
                        <select
                            className="form-control"
                            value={mode}
                            onChange={(e) => this.setState({ mode: e.target.value })}
                            disabled={uploading}
                        >
                            <option value="rgb">{_("RGB")}</option>
                            <option value="intensity">{_("Intensity")}</option>
                            <option value="elevation">{_("Elevation")}</option>
                            <option value="count">{_("Point Count")}</option>
                        </select>
                    </div>
                    
                    <div className="form-group">
                        <label>{_("Resolution (meters)")}</label>
                        <input
                            type="number"
                            className="form-control"
                            step="0.01"
                            min="0.01"
                            value={resolution}
                            onChange={(e) => this.setState({ resolution: parseFloat(e.target.value) })}
                            disabled={uploading}
                        />
                    </div>
                    
                    <div className="form-group">
                        <label>
                            <input
                                type="checkbox"
                                checked={multiview}
                                onChange={(e) => this.setState({ multiview: e.target.checked })}
                                disabled={uploading}
                            />
                            {' '}{_("Create Multiple Viewpoints")}
                        </label>
                        {multiview && (
                            <div className="ml-4 mt-2">
                                <div className="form-group">
                                    <label>{_("Tile Size (meters)")}</label>
                                    <input
                                        type="number"
                                        className="form-control"
                                        value={tileSize}
                                        onChange={(e) => this.setState({ tileSize: parseFloat(e.target.value) })}
                                        disabled={uploading}
                                    />
                                </div>
                                <div className="form-group">
                                    <label>{_("Overlap")} (0.0-1.0)</label>
                                    <input
                                        type="number"
                                        className="form-control"
                                        step="0.1"
                                        min="0"
                                        max="1"
                                        value={overlap}
                                        onChange={(e) => this.setState({ overlap: parseFloat(e.target.value) })}
                                        disabled={uploading}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                    
                    <div className="form-group">
                        <label>
                            <input
                                type="checkbox"
                                checked={convertToJpg}
                                onChange={(e) => this.setState({ convertToJpg: e.target.checked })}
                                disabled={uploading}
                            />
                            {' '}{_("Convert to JPEG")}
                        </label>
                        <small className="text-muted d-block">
                            {_("JPEG format is more compatible with WebODM")}
                        </small>
                    </div>
                    
                    <button
                        type="submit"
                        className="btn btn-primary"
                        disabled={uploading || !file}
                    >
                        {uploading ? (
                            <>
                                <i className="fa fa-spin fa-circle-notch"></i> {_("Converting...")}
                            </>
                        ) : (
                            <>
                                <i className="fa fa-upload"></i> {_("Convert")}
                            </>
                        )}
                    </button>
                </form>
                
                {error && (
                    <div className="alert alert-danger mt-3">
                        <strong>{_("Error:")}</strong> {error}
                    </div>
                )}
                
                {result && (
                    <div className="alert alert-success mt-3">
                        <h4>{_("Conversion Successful!")}</h4>
                        <p>{result.message}</p>
                        <p>
                            <strong>{_("Files created:")}</strong> {result.count}
                        </p>
                        <button
                            className="btn btn-success"
                            onClick={() => this.handleDownload(result.download_url, 'converted_images.zip')}
                        >
                            <i className="fa fa-download"></i> {_("Download ZIP")}
                        </button>
                        {result.files && result.files.length > 0 && (
                            <div className="mt-2">
                                <strong>{_("Files:")}</strong>
                                <ul>
                                    {result.files.slice(0, 10).map((f, i) => (
                                        <li key={i}>{f}</li>
                                    ))}
                                    {result.files.length > 10 && (
                                        <li>... {_("and")} {result.files.length - 10} {_("more")}</li>
                                    )}
                                </ul>
                            </div>
                        )}
                    </div>
                )}
            </div>
        );
    }
}

